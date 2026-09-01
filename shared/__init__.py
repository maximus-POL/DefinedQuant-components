"""Defined Quant public runtime API."""

import sys
from pathlib import Path

# Hatch maps this readable source folder to the installed ``defined_quant`` package. Its editable
# wheel adds the two source directories to ``sys.path``; extend this package's own search path so
# both halves remain live without a generated ``defined_quant/`` source tree.
for _entry in sys.path:
    _candidate = Path(_entry)
    if (
        _candidate.name == "shared"
        and (_candidate / "catalog.py").is_file()
        and (_candidate / "validation.py").is_file()
    ):
        __path__.insert(0, str(_candidate))
    elif _candidate.name == "categories" and (_candidate / "README.md").is_file():
        __path__.append(str(_candidate))

from defined_quant.catalog import (  # noqa: E402
    component_models,
    component_record,
    invalidate_subject_cache,
    iter_components,
    load_component,
    subject_hash,
    subject_manifest,
    verify_subject,
)
from defined_quant.charts import render_svg, save_svg, visualization_hash  # noqa: E402
from defined_quant.discovery import (  # noqa: E402
    ContractIndex,
    DiscoveryFilters,
    FacetValue,
    FieldMatch,
    SearchHit,
    SearchResults,
    catalog_facets,
    component_facets,
    search_components,
    tokenize,
)
from defined_quant.method_registry import (  # noqa: E402
    GovernedRegistry,
    project_component_inspection,
)
from defined_quant.plan_compiler import (  # noqa: E402
    RESOLUTION_ALGORITHM_VERSION,
    compile_plan,
)
from defined_quant.plan_validation import (  # noqa: E402
    create_authorization_binding,
    create_manual_approval,
    load_execution_policy,
    validate_plan,
    verify_authorization,
)
from defined_quant.validation import preflight  # noqa: E402

__version__ = "0.1.3"

__all__ = [
    "__version__",
    "ContractIndex",
    "DiscoveryFilters",
    "FacetValue",
    "FieldMatch",
    "GovernedRegistry",
    "RESOLUTION_ALGORITHM_VERSION",
    "SearchHit",
    "SearchResults",
    "catalog_facets",
    "component_models",
    "component_record",
    "component_facets",
    "create_authorization_binding",
    "create_manual_approval",
    "compile_plan",
    "invalidate_subject_cache",
    "iter_components",
    "load_component",
    "load_execution_policy",
    "preflight",
    "project_component_inspection",
    "render_svg",
    "save_svg",
    "search_components",
    "subject_hash",
    "subject_manifest",
    "tokenize",
    "validate_plan",
    "verify_subject",
    "verify_authorization",
    "visualization_hash",
]

del _candidate, _entry
