from __future__ import annotations

import json
from pathlib import Path

from defined_quant.registry import load_registry

from authoring.export_component_pages import (
    component_pages,
    export_component_pages,
)

ROOT = Path(__file__).resolve().parents[2]
EXPECTED_URLS = {
    "dq.market_data.log_return": "/components/log-return",
    "dq.market_data.monthly_return_matrix": "/components/monthly-return-matrix",
    "dq.market_data.rebased_price_index": "/components/rebased-price-index",
    "dq.market_data.simple_return": "/components/simple-return",
    "dq.performance.drawdown": "/components/drawdown",
    "dq.volatility.historical_volatility": "/components/historical-volatility",
    "dq.volatility.rolling_historical_volatility": (
        "/components/rolling-historical-volatility"
    ),
}


def test_component_page_export_is_deterministic_registry_only_and_website_safe(
    tmp_path: Path,
) -> None:
    registry = load_registry(root=ROOT / "registry")
    first = component_pages(registry, commit_sha="a" * 40)
    second = component_pages(registry, commit_sha="a" * 40)

    assert first == second
    assert first["artifact_kind"] == "defined_quant_component_pages"
    assert "component_pages" in first
    assert "components" not in first
    pages = {page["id"]: page for page in first["component_pages"]}
    assert {identifier: page["url"] for identifier, page in pages.items()} == EXPECTED_URLS
    assert {
        identifier: page["slug"] for identifier, page in pages.items()
    } == {
        identifier: url.removeprefix("/components/")
        for identifier, url in EXPECTED_URLS.items()
    }

    page = pages["dq.market_data.simple_return"]
    assert page["id"] == "dq.market_data.simple_return"
    assert page["slug"] == "simple-return"
    assert page["url"] == "/components/simple-return"
    assert page["method"]["contract_hash"]
    assert page["capabilities"][0]["id"] == "returns.simple"
    assert page["registered_implementations"][0]["support_status"] == "registered"
    assert (
        page["registered_implementations"][0]["runtime_availability"]
        == "installation_specific_not_exported"
    )
    assert page["evidence"]["method"]
    assert page["evidence"]["capability_conformance"]
    assert page["evidence"]["implementation"]
    assert page["evidence"]["adapter"]

    for exported in pages.values():
        assert exported["evidence"]["method"]
        assert exported["evidence"]["capability_conformance"]
        assert set(exported["evidence"]) == {
            "adapter",
            "backend",
            "capability_conformance",
            "implementation",
            "method",
        }
        for implementation in exported["registered_implementations"]:
            assert implementation["support_status"] == "registered"
            assert (
                implementation["runtime_availability"]
                == "installation_specific_not_exported"
            )

    encoded = json.dumps(first, ensure_ascii=False, sort_keys=True, allow_nan=False)
    assert "dispatch_key" not in encoded
    assert "dq_native_simple_return" not in encoded
    assert str(ROOT) not in encoded

    output = tmp_path / "component-pages.json"
    export_component_pages(first, output)
    original = output.read_bytes()
    export_component_pages(second, output)
    assert output.read_bytes() == original
    assert original.endswith(b"\n")
