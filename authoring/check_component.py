#!/usr/bin/env python3
"""Validate the readable category/component catalog and its evidence bindings."""

from __future__ import annotations

import argparse
import ast
import importlib
import importlib.util
import inspect
import json
import os
import re
import subprocess
import sys
import tempfile
import types
import unicodedata
import xml.etree.ElementTree as ET
from collections.abc import Callable, Iterable, Mapping
from pathlib import Path
from typing import Any

import jsonschema  # type: ignore[import-untyped]
import yaml  # type: ignore[import-untyped]
from pydantic import BaseModel

REQUIRED_COMPONENT_FILES = {
    "README.md",
    "component.py",
    "contract.yaml",
    "evidence.yaml",
    "test_component.py",
}
OPTIONAL_FILES = {"resources.md", "review.yaml"}
OPTIONAL_DIRECTORIES = {"examples", "visuals"}
IGNORED_NAMES = {".DS_Store", "__pycache__"}
LEGACY_NAMES = {
    "component.yaml",
    "core.py",
    "evals.yaml",
    "explanation.md",
    "guidance.yaml",
    "presentation.yaml",
    "validation.yaml",
}
EVIDENCE_SECTIONS = ("known_answers", "invariants", "boundary_cases", "cross_checks")
FORBIDDEN_CONTRACT_FIELDS = {
    "inputs",
    "output",
    "status",
    "subject_hash",
    "evidence_hash",
}
IDENTIFIER = re.compile(r"^[a-z][a-z0-9]*(?:_[a-z0-9]+)*$")
DISCOVERY_LIMITS = {
    "aliases": 16,
    "intents": 12,
    "input_concepts": 24,
    "output_concepts": 24,
}
DISCOVERY_IDENTIFIER_FIELDS = {"intents", "input_concepts", "output_concepts"}


class ContractLoader(yaml.SafeLoader):  # type: ignore[misc]
    """Load ISO dates as strings so JSON Schema date validation remains explicit."""


ContractLoader.yaml_implicit_resolvers = {
    key: [(tag, pattern) for tag, pattern in resolvers if tag != "tag:yaml.org,2002:timestamp"]
    for key, resolvers in yaml.SafeLoader.yaml_implicit_resolvers.items()
}


def _project_root(override: Path | None) -> Path:
    return override.resolve() if override else Path(__file__).resolve().parents[1]


def _is_within(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
    except ValueError:
        return False
    return True


def _load_mapping(path: Path) -> dict[str, Any]:
    try:
        text = path.read_text(encoding="utf-8")
        try:
            value = json.loads(text)
        except json.JSONDecodeError:
            value = yaml.load(text, Loader=ContractLoader)
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        raise ValueError(f"cannot read YAML: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError("file must contain one mapping")
    return value


def _load_schema(root: Path, filename: str) -> dict[str, Any]:
    path = root / "authoring" / "schemas" / filename
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read schema {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain one JSON object")
    validator_class = jsonschema.validators.validator_for(value)
    validator_class.check_schema(value)
    return value


def _schema_errors(
    root: Path,
    path: Path,
    value: dict[str, Any],
    schema_name: str,
) -> list[str]:
    try:
        schema = _load_schema(root, schema_name)
    except (ValueError, jsonschema.SchemaError) as exc:
        return [f"{path}: {exc}"]
    validator_class = jsonschema.validators.validator_for(schema)
    validator = validator_class(schema, format_checker=jsonschema.FormatChecker())
    errors: list[str] = []
    for error in sorted(validator.iter_errors(value), key=lambda item: list(item.path)):
        location = ".".join(str(part) for part in error.absolute_path) or "<root>"
        errors.append(f"{path}:{location}: {error.message}")
    return errors


def _read_front_matter(path: Path) -> dict[str, Any]:
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise ValueError(f"cannot read README: {exc}") from exc
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        raise ValueError("README.md must start with YAML front matter delimited by ---")
    try:
        closing = next(
            index for index, line in enumerate(lines[1:], start=1) if line.strip() == "---"
        )
    except StopIteration as exc:
        raise ValueError("README.md front matter has no closing ---") from exc
    try:
        value = yaml.load("\n".join(lines[1:closing]), Loader=ContractLoader)
    except yaml.YAMLError as exc:
        raise ValueError(f"invalid README.md front matter: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError("README.md front matter must contain one mapping")
    return value


def _resolve_targets(
    root: Path, raw_targets: list[str]
) -> tuple[list[Path], list[Path], list[str]]:
    categories_root = root / "categories"
    categories: set[Path] = set()
    components: set[Path] = set()
    errors: list[str] = []

    if not categories_root.is_dir():
        return [], [], [f"{categories_root}: categories directory is missing"]

    if not raw_targets:
        for path in sorted(categories_root.iterdir()):
            if path.is_dir() and not path.name.startswith("."):
                categories.add(path)
                for child in sorted(path.iterdir()):
                    if child.is_dir() and not child.name.startswith("."):
                        components.add(child)
        for contract in categories_root.rglob("contract.yaml"):
            relative = contract.parent.relative_to(categories_root)
            if len(relative.parts) != 2:
                errors.append(
                    f"{contract.parent}: components must be exactly "
                    "categories/<category>/<component>"
                )
        return sorted(categories), sorted(components), errors

    for raw in raw_targets:
        candidate = Path(raw)
        path = candidate if candidate.is_absolute() else root / candidate
        path = path.resolve()
        if not _is_within(path, categories_root):
            errors.append(f"{raw}: target must be inside {categories_root}")
            continue
        if path.is_file():
            path = path.parent
        if not path.is_dir():
            errors.append(f"{raw}: target does not exist")
            continue
        relative = path.relative_to(categories_root)
        if len(relative.parts) == 1:
            categories.add(path)
            components.update(
                child
                for child in path.iterdir()
                if child.is_dir() and not child.name.startswith(".")
            )
        elif len(relative.parts) == 2:
            categories.add(path.parent)
            components.add(path)
        else:
            errors.append(
                f"{raw}: component targets must be exactly categories/<category>/<component>"
            )
    return sorted(categories), sorted(components), errors


def _validate_category(category_dir: Path) -> tuple[dict[str, Any] | None, list[str]]:
    readme = category_dir / "README.md"
    errors: list[str] = []
    if not IDENTIFIER.fullmatch(category_dir.name):
        errors.append(f"{category_dir}: category folder must use lower snake_case")
    if not readme.is_file():
        return None, [*errors, f"{readme}: required category README is missing"]
    try:
        metadata = _read_front_matter(readme)
    except ValueError as exc:
        return None, [*errors, f"{readme}: {exc}"]

    allowed = {"id", "title", "summary"}
    missing = allowed - set(metadata)
    extra = set(metadata) - allowed
    if missing:
        errors.append(f"{readme}: missing front matter fields: {', '.join(sorted(missing))}")
    if extra:
        errors.append(f"{readme}: unknown front matter fields: {', '.join(sorted(extra))}")
    if metadata.get("id") != category_dir.name:
        errors.append(f"{readme}: id must equal category folder {category_dir.name!r}")
    title = metadata.get("title")
    summary = metadata.get("summary")
    if not isinstance(title, str) or not title.strip() or len(title) > 100:
        errors.append(f"{readme}: title must be a non-empty string of at most 100 characters")
    if not isinstance(summary, str) or not summary.strip() or len(summary) > 240:
        errors.append(f"{readme}: summary must be a non-empty string of at most 240 characters")
    return metadata, errors


def _validate_discovery(path: Path, value: object) -> list[str]:
    """Apply normalized uniqueness and controlled-vocabulary shape checks.

    JSON Schema catches ordinary structural errors.  These checks additionally prevent aliases
    that differ only by Unicode form, case, or repeated whitespace from polluting the index.
    """

    if not isinstance(value, Mapping):
        return [f"{path}:discovery: must be an object"]

    errors: list[str] = []
    expected = set(DISCOVERY_LIMITS)
    missing = expected - set(value)
    extra = set(value) - expected
    if missing:
        errors.append(
            f"{path}:discovery: missing fields: {', '.join(sorted(missing))}"
        )
    if extra:
        errors.append(
            f"{path}:discovery: unknown fields: {', '.join(sorted(extra))}"
        )

    for field, maximum in DISCOVERY_LIMITS.items():
        raw_values = value.get(field)
        if not isinstance(raw_values, list):
            continue
        if not 1 <= len(raw_values) <= maximum:
            errors.append(
                f"{path}:discovery.{field}: must contain between 1 and {maximum} values"
            )
        normalized: dict[str, int] = {}
        for index, raw in enumerate(raw_values):
            if not isinstance(raw, str):
                continue
            normalized_value = " ".join(
                unicodedata.normalize("NFKC", raw).casefold().split()
            )
            if not normalized_value:
                errors.append(
                    f"{path}:discovery.{field}[{index}]: values must not be blank"
                )
                continue
            if normalized_value in normalized:
                errors.append(
                    f"{path}:discovery.{field}: values at indices "
                    f"{normalized[normalized_value]} and {index} are duplicates after normalization"
                )
            else:
                normalized[normalized_value] = index
            if field in DISCOVERY_IDENTIFIER_FIELDS and not IDENTIFIER.fullmatch(raw):
                errors.append(
                    f"{path}:discovery.{field}[{index}]: concepts and intents must use "
                    "lower snake_case identifiers"
                )
    return errors


def _parse_python(path: Path) -> tuple[ast.Module | None, list[str]]:
    try:
        return ast.parse(path.read_text(encoding="utf-8"), filename=str(path)), []
    except (OSError, UnicodeError, SyntaxError) as exc:
        return None, [f"{path}: invalid Python: {exc}"]


def _inspect_component_python(component_dir: Path, contract: Mapping[str, Any]) -> list[str]:
    path = component_dir / "component.py"
    tree, errors = _parse_python(path)
    if tree is None:
        return errors

    callable_path = contract.get("callable")
    callable_name = (
        callable_path.rsplit(":", 1)[1]
        if isinstance(callable_path, str) and ":" in callable_path
        else None
    )
    functions = {
        node.name for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    if callable_name and callable_name not in functions:
        errors.append(f"{path}: declared callable {callable_name!r} is not defined")

    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id in {"eval", "exec"}
        ):
            errors.append(f"{path}:{node.lineno}: eval/exec is forbidden")
    return errors


def _bootstrap_checkout_package(root: Path) -> None:
    """Expose ``shared/`` and ``categories/`` as one local ``defined_quant`` package."""

    if "defined_quant" in sys.modules:
        return
    shared = root / "shared"
    categories = root / "categories"
    initializer = shared / "__init__.py"
    if not initializer.is_file():
        raise ImportError(f"missing shared package initializer: {initializer}")
    spec = importlib.util.spec_from_file_location(
        "defined_quant",
        initializer,
        submodule_search_locations=[str(shared), str(categories)],
    )
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot create package spec from {initializer}")
    module = importlib.util.module_from_spec(spec)
    if not isinstance(module, types.ModuleType):
        raise ImportError(f"cannot create package module from {initializer}")
    sys.modules["defined_quant"] = module
    try:
        spec.loader.exec_module(module)
    except Exception:
        sys.modules.pop("defined_quant", None)
        raise


def _validate_callable_signature(
    component: Callable[..., Any],
    input_fields: set[str],
) -> None:
    """Require a callable that can receive every validated input as a keyword."""

    try:
        signature = inspect.signature(component)
    except (TypeError, ValueError) as exc:
        raise ValueError("component callable signature cannot be inspected") from exc

    parameters = tuple(signature.parameters.values())
    positional_only = sorted(
        parameter.name
        for parameter in parameters
        if parameter.kind is inspect.Parameter.POSITIONAL_ONLY
    )
    accepts_arbitrary_keywords = any(
        parameter.kind is inspect.Parameter.VAR_KEYWORD for parameter in parameters
    )
    accepted_keywords = {
        parameter.name
        for parameter in parameters
        if parameter.kind
        in (inspect.Parameter.POSITIONAL_OR_KEYWORD, inspect.Parameter.KEYWORD_ONLY)
    }
    missing_inputs = (
        set() if accepts_arbitrary_keywords else input_fields - accepted_keywords
    )
    required_extras = sorted(
        parameter.name
        for parameter in parameters
        if parameter.kind
        in (inspect.Parameter.POSITIONAL_OR_KEYWORD, inspect.Parameter.KEYWORD_ONLY)
        and parameter.default is inspect.Parameter.empty
        and parameter.name not in input_fields
    )

    problems: list[str] = []
    if positional_only:
        problems.append(
            "positional-only parameters are not allowed: " + ", ".join(positional_only)
        )
    if missing_inputs:
        problems.append(
            "Inputs fields are not accepted as keywords: " + ", ".join(sorted(missing_inputs))
        )
    if required_extras:
        problems.append(
            "required parameters are absent from Inputs: " + ", ".join(required_extras)
        )
    if problems:
        raise ValueError(
            "component callable is incompatible with canonical Inputs; " + "; ".join(problems)
        )


def _validate_output_model(module_name: str, output: Any) -> type[BaseModel]:
    """Require the shared output envelope used by every catalog and host adapter."""

    from defined_quant.types import ComponentOutput

    if not isinstance(output, type) or not issubclass(output, BaseModel):
        raise ValueError(f"{module_name}.Output must be a Pydantic model")
    if not issubclass(output, ComponentOutput):
        raise ValueError(
            f"{module_name}.Output must extend defined_quant.types.ComponentOutput"
        )
    return output


def _import_contract_models(
    root: Path, contract: Mapping[str, Any]
) -> tuple[set[str], set[str], dict[str, Any], set[str]]:
    _bootstrap_checkout_package(root)

    callable_path = contract.get("callable")
    if not isinstance(callable_path, str) or ":" not in callable_path:
        raise ValueError("contract callable is invalid")
    module_name, callable_name = callable_path.split(":", maxsplit=1)
    module = importlib.import_module(module_name)
    inputs = getattr(module, "Inputs", None)
    output = getattr(module, "Output", None)
    component = getattr(module, callable_name, None)
    if not isinstance(inputs, type) or not issubclass(inputs, BaseModel):
        raise ValueError(f"{module_name}.Inputs must be a Pydantic model")
    output_model = _validate_output_model(module_name, output)
    if not callable(component):
        raise ValueError(f"{callable_path} does not resolve to a callable")

    input_fields = set(inputs.model_fields)
    _validate_callable_signature(component, input_fields)
    required_inputs = {name for name, field in inputs.model_fields.items() if field.is_required()}
    defaults = {
        name: field.default
        for name, field in inputs.model_fields.items()
        if not field.is_required()
    }
    return input_fields, required_inputs, defaults, set(output_model.model_fields)


def _condition_fields(value: Any) -> Iterable[str]:
    if isinstance(value, Mapping):
        field = value.get("field")
        if isinstance(field, str):
            yield field
        for child in value.values():
            yield from _condition_fields(child)
    elif isinstance(value, list):
        for child in value:
            yield from _condition_fields(child)


def _duplicates(values: Iterable[str]) -> set[str]:
    seen: set[str] = set()
    duplicates: set[str] = set()
    for value in values:
        if value in seen:
            duplicates.add(value)
        seen.add(value)
    return duplicates


def _validate_guidance(
    path: Path,
    guidance: Any,
    input_fields: set[str],
    required_inputs: set[str],
    input_defaults: Mapping[str, Any],
) -> list[str]:
    if not isinstance(guidance, Mapping):
        return [f"{path}: guidance must be an object"]
    errors: list[str] = []
    referenced = set(_condition_fields(guidance.get("constraints", [])))
    referenced.update(_condition_fields(guidance.get("allowed_defaults", [])))
    for field in sorted(referenced - input_fields):
        errors.append(f"{path}: guidance references unknown input field {field!r}")

    raw_questions = guidance.get("required_questions", [])
    questions = raw_questions if isinstance(raw_questions, list) else []
    question_ids: list[str] = []
    for item in questions:
        if not isinstance(item, Mapping):
            continue
        question_id = item.get("id")
        if isinstance(question_id, str):
            question_ids.append(question_id)
    for duplicate in sorted(_duplicates(question_ids)):
        errors.append(f"{path}: duplicate required question id {duplicate!r}")
    for question in questions:
        if not isinstance(question, Mapping):
            continue
        target = question.get("resolves_to")
        if isinstance(target, str) and target not in input_fields:
            errors.append(f"{path}: required question resolves to unknown input {target!r}")
        elif isinstance(target, str) and target not in required_inputs:
            errors.append(f"{path}: required question resolves to non-required input {target!r}")

    raw_defaults = guidance.get("allowed_defaults", [])
    defaults = raw_defaults if isinstance(raw_defaults, list) else []
    default_fields: list[str] = []
    for item in defaults:
        if not isinstance(item, Mapping):
            continue
        default_field = item.get("field")
        if isinstance(default_field, str):
            default_fields.append(default_field)
    for duplicate in sorted(_duplicates(default_fields)):
        errors.append(f"{path}: duplicate allowed default for {duplicate!r}")
    for field in sorted(set(default_fields) - input_fields):
        errors.append(f"{path}: allowed default references unknown input {field!r}")
    for field in sorted(set(default_fields) & required_inputs):
        errors.append(f"{path}: allowed default for {field!r} is not declared by Inputs")
    for item in defaults:
        if not isinstance(item, Mapping):
            continue
        allowed_field = item.get("field")
        if not isinstance(allowed_field, str) or allowed_field not in input_defaults:
            continue
        model_default = input_defaults[allowed_field]
        if hasattr(model_default, "value"):
            model_default = model_default.value
        if model_default != item.get("value"):
            errors.append(
                f"{path}: allowed default for {allowed_field!r} does not match Inputs"
            )
    return errors


def _test_functions(component_dir: Path) -> set[str]:
    path = component_dir / "test_component.py"
    tree, _ = _parse_python(path)
    if tree is None:
        return set()
    return {
        node.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }


def _validate_evidence(
    path: Path,
    evidence: Mapping[str, Any],
    component_dir: Path,
    output_fields: set[str],
) -> list[str]:
    errors: list[str] = []
    test_functions = _test_functions(component_dir)
    evidence_ids: list[str] = []
    test_ids: list[str] = []
    for section in EVIDENCE_SECTIONS:
        records = evidence.get(section, [])
        if not isinstance(records, list):
            continue
        for record in records:
            if not isinstance(record, Mapping):
                continue
            record_id = record.get("id")
            test_id = record.get("test_id")
            if isinstance(record_id, str):
                evidence_ids.append(record_id)
            if isinstance(test_id, str):
                test_ids.append(test_id)
                if test_id not in test_functions:
                    errors.append(
                        f"{path}: test_id {test_id!r} is not defined in test_component.py"
                    )
    for duplicate in sorted(_duplicates(evidence_ids)):
        errors.append(f"{path}: duplicate evidence id {duplicate!r}")
    for duplicate in sorted(_duplicates(test_ids)):
        errors.append(f"{path}: duplicate evidence test_id {duplicate!r}")

    agent_ids: list[str] = []
    for case in evidence.get("agent_cases", []):
        if not isinstance(case, Mapping):
            continue
        case_id = case.get("id")
        if isinstance(case_id, str):
            agent_ids.append(case_id)
        for field in case.get("expected_fields", []):
            if field not in output_fields:
                errors.append(f"{path}: agent case expected field {field!r} is absent from Output")
    for duplicate in sorted(_duplicates(agent_ids)):
        errors.append(f"{path}: duplicate agent case id {duplicate!r}")
    return errors


def _validate_optional_content(component_dir: Path) -> list[str]:
    errors: list[str] = []
    for name in OPTIONAL_FILES:
        path = component_dir / name
        if path.exists():
            try:
                valid = path.is_file() and bool(path.read_text(encoding="utf-8").strip())
            except (OSError, UnicodeError):
                valid = False
            if not valid:
                errors.append(f"{path}: optional files must be absent or non-empty")
    for name in OPTIONAL_DIRECTORIES:
        path = component_dir / name
        if path.exists():
            contains_file = path.is_dir() and any(item.is_file() for item in path.rglob("*"))
            if not contains_file:
                errors.append(f"{path}: optional directories must be absent or non-empty")

    allowed = REQUIRED_COMPONENT_FILES | OPTIONAL_FILES | OPTIONAL_DIRECTORIES | IGNORED_NAMES
    for path in component_dir.iterdir():
        if path.name in LEGACY_NAMES:
            errors.append(
                f"{path}: legacy component file is not allowed; use the five-file contract"
            )
        elif path.name not in allowed and not path.name.startswith("."):
            errors.append(f"{path}: unknown component entry")
    return errors


def _subject_hash(component_dir: Path) -> str:
    importlib.invalidate_caches()
    module = importlib.import_module("defined_quant.catalog")
    function = getattr(module, "subject_hash")
    digest = function(component_dir)
    if not isinstance(digest, str):
        raise TypeError("defined_quant.catalog.subject_hash did not return a string")
    return digest.removeprefix("sha256:")


def _validate_component(
    root: Path,
    component_dir: Path,
    *,
    check_binding: bool,
) -> tuple[dict[str, Any] | None, list[str]]:
    categories_root = root / "categories"
    errors: list[str] = []
    try:
        relative = component_dir.relative_to(categories_root)
    except ValueError:
        return None, [f"{component_dir}: component must be under {categories_root}"]
    if len(relative.parts) != 2:
        errors.append(
            f"{component_dir}: components must be exactly categories/<category>/<component>"
        )
    if not IDENTIFIER.fullmatch(component_dir.name):
        errors.append(f"{component_dir}: component folder must use lower snake_case")

    for filename in sorted(REQUIRED_COMPONENT_FILES):
        path = component_dir / filename
        if not path.is_file():
            errors.append(f"{path}: required component file is missing")
        else:
            try:
                if not path.read_text(encoding="utf-8").strip():
                    errors.append(f"{path}: required component file is empty")
            except (OSError, UnicodeError) as exc:
                errors.append(f"{path}: cannot read required file: {exc}")
    errors.extend(_validate_optional_content(component_dir))

    contract_path = component_dir / "contract.yaml"
    evidence_path = component_dir / "evidence.yaml"
    try:
        contract = _load_mapping(contract_path)
    except ValueError as exc:
        return None, [*errors, f"{contract_path}: {exc}"]
    errors.extend(_schema_errors(root, contract_path, contract, "contract.schema.json"))
    errors.extend(_validate_discovery(contract_path, contract.get("discovery")))

    evidence: dict[str, Any] | None = None
    try:
        evidence = _load_mapping(evidence_path)
    except ValueError as exc:
        errors.append(f"{evidence_path}: {exc}")
    else:
        errors.extend(_schema_errors(root, evidence_path, evidence, "evidence.schema.json"))

    forbidden = sorted(FORBIDDEN_CONTRACT_FIELDS.intersection(contract))
    if forbidden:
        errors.append(f"{contract_path}: derived/type fields are forbidden: {', '.join(forbidden)}")

    category = component_dir.parent.name
    slug = component_dir.name
    component_id = f"dq.{category}.{slug}"
    expected_callable = f"defined_quant.{category}.{slug}.component:{slug}"
    for field, expected in (
        ("category", category),
        ("slug", slug),
        ("id", component_id),
        ("callable", expected_callable),
    ):
        if contract.get(field) != expected:
            errors.append(
                f"{contract_path}: {field} must be {expected!r}, got {contract.get(field)!r}"
            )
    template = contract.get("template")
    display = contract.get("display")
    if (
        isinstance(template, Mapping)
        and isinstance(display, Mapping)
        and template.get("profile") != display.get("profile")
    ):
        errors.append(f"{contract_path}: display.profile must match template.profile")

    errors.extend(_inspect_component_python(component_dir, contract))
    try:
        input_fields, required_inputs, input_defaults, output_fields = _import_contract_models(
            root, contract
        )
    except (ImportError, AttributeError, TypeError, ValueError) as exc:
        errors.append(f"{component_dir / 'component.py'}: cannot load models: {exc}")
        input_fields, required_inputs, input_defaults, output_fields = (
            set(),
            set(),
            {},
            set(),
        )
    errors.extend(
        _validate_guidance(
            contract_path,
            contract.get("guidance"),
            input_fields,
            required_inputs,
            input_defaults,
        )
    )

    if evidence is not None:
        if evidence.get("component_id") != component_id:
            errors.append(f"{evidence_path}: component_id must be {component_id!r}")
        errors.extend(_validate_evidence(evidence_path, evidence, component_dir, output_fields))
        binding = evidence.get("validated_subject_hash")
        if binding is None and contract.get("lifecycle") == "published":
            errors.append(f"{evidence_path}: published components need validated_subject_hash")
        elif check_binding and isinstance(binding, str):
            try:
                current = _subject_hash(component_dir)
            except Exception as exc:
                errors.append(f"{evidence_path}: cannot recompute subject hash: {exc}")
            else:
                if binding.removeprefix("sha256:") != current:
                    errors.append(
                        f"{evidence_path}: validated_subject_hash is stale; expected {current}"
                    )
    return contract, errors


def validate(
    root: Path,
    raw_targets: list[str],
    *,
    check_bindings: bool = True,
) -> tuple[list[Path], list[str]]:
    categories, components, errors = _resolve_targets(root, raw_targets)
    seen_categories: dict[str, Path] = {}
    seen_components: dict[str, Path] = {}

    for category_dir in categories:
        metadata, category_errors = _validate_category(category_dir)
        errors.extend(category_errors)
        if metadata is not None and isinstance(metadata.get("id"), str):
            category_id = metadata["id"]
            if category_id in seen_categories:
                errors.append(
                    f"{category_dir}: duplicate category id {category_id!r}; "
                    f"first seen at {seen_categories[category_id]}"
                )
            seen_categories[category_id] = category_dir

    for component_dir in components:
        contract, component_errors = _validate_component(
            root, component_dir, check_binding=check_bindings
        )
        errors.extend(component_errors)
        if contract is not None and isinstance(contract.get("id"), str):
            component_id = contract["id"]
            if component_id in seen_components:
                errors.append(
                    f"{component_dir}: duplicate component id {component_id!r}; "
                    f"first seen at {seen_components[component_id]}"
                )
            seen_components[component_id] = component_dir
    return components, errors


def _evidence_test_ids(evidence: Mapping[str, Any]) -> set[str]:
    ids: set[str] = set()
    for section in EVIDENCE_SECTIONS:
        for record in evidence.get(section, []):
            if isinstance(record, Mapping) and isinstance(record.get("test_id"), str):
                ids.add(record["test_id"])
    return ids


def _run_component_tests(root: Path, component_dir: Path) -> tuple[int, set[str], set[str]]:
    environment = os.environ.copy()
    with tempfile.TemporaryDirectory(prefix="defined-quant-evidence-") as temporary:
        report = Path(temporary) / "pytest.xml"
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "pytest",
                str(component_dir / "test_component.py"),
                f"--junitxml={report}",
            ],
            cwd=root,
            env=environment,
            check=False,
        )
        if not report.is_file():
            return result.returncode, set(), set()
        try:
            document = ET.parse(report).getroot()
        except (ET.ParseError, OSError):
            return result.returncode, set(), set()

    executed: set[str] = set()
    skipped: set[str] = set()
    for case in document.iter("testcase"):
        raw_name = case.get("name")
        if not raw_name:
            continue
        name = raw_name.split("[", maxsplit=1)[0]
        executed.add(name)
        if case.find("skipped") is not None:
            skipped.add(name)
    return result.returncode, executed, skipped


def _contains_todo(component_dir: Path) -> bool:
    for filename in REQUIRED_COMPONENT_FILES:
        try:
            if "TODO" in (component_dir / filename).read_text(encoding="utf-8"):
                return True
        except (OSError, UnicodeError):
            return True
    return False


def _bless(root: Path, raw_path: str) -> int:
    components, errors = validate(root, [raw_path], check_bindings=False)
    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1
    if len(components) != 1:
        print("ERROR: --bless requires exactly one component", file=sys.stderr)
        return 1
    component_dir = components[0]
    if _contains_todo(component_dir):
        print("ERROR: replace every TODO before blessing evidence", file=sys.stderr)
        return 1

    evidence_path = component_dir / "evidence.yaml"
    evidence = _load_mapping(evidence_path)
    expected = _evidence_test_ids(evidence)
    if not expected:
        print("ERROR: add at least one evidence record before blessing", file=sys.stderr)
        return 1

    exit_code, executed, skipped = _run_component_tests(root, component_dir)
    if exit_code:
        print("ERROR: component tests failed; evidence was not blessed", file=sys.stderr)
        return 1
    missing = expected - executed
    if missing:
        print(
            "ERROR: evidence tests were not executed: " + ", ".join(sorted(missing)),
            file=sys.stderr,
        )
        return 1
    skipped_evidence = expected & skipped
    if skipped_evidence:
        print(
            "ERROR: evidence tests were skipped: " + ", ".join(sorted(skipped_evidence)),
            file=sys.stderr,
        )
        return 1

    try:
        digest = _subject_hash(component_dir)
    except Exception as exc:
        print(f"ERROR: could not compute subject hash: {exc}", file=sys.stderr)
        return 1
    evidence["validated_subject_hash"] = digest
    evidence_path.write_text(
        json.dumps(evidence, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    _, post_errors = validate(root, [str(component_dir)])
    if post_errors:
        for error in post_errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1
    print(f"Blessed {component_dir} with subject hash {digest}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("targets", nargs="*", help="optional category/component paths")
    parser.add_argument("--bless", metavar="COMPONENT")
    parser.add_argument("--root", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.bless and args.targets:
        parser.error("--bless cannot be combined with positional targets")

    root = _project_root(args.root)
    if args.bless:
        return _bless(root, args.bless)

    components, errors = validate(root, args.targets)
    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        print(f"Catalog check failed with {len(errors)} error(s).", file=sys.stderr)
        return 1
    scope = "catalog" if not args.targets else ", ".join(args.targets)
    print(f"Catalog check passed for {scope} ({len(components)} component(s)).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
