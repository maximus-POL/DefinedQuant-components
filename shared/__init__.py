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
    elif (
        _candidate.name == "categories"
        and (_candidate / "README.md").is_file()
    ):
        __path__.append(str(_candidate))

from defined_quant.catalog import (  # noqa: E402
    component_models,
    component_record,
    iter_components,
    load_component,
    subject_hash,
    subject_manifest,
)
from defined_quant.charts import (  # noqa: E402
    DashboardSpec,
    RenderTarget,
    TableColumn,
    TableRow,
    TableSpec,
    ViewBundleSpec,
    dashboard_hash,
    render_dashboard_html,
    render_dashboard_svg,
    render_svg,
    save_dashboard_html,
    save_dashboard_svg,
    save_svg,
    select_view,
    view_hash,
    visualization_hash,
)
from defined_quant.discovery import (  # noqa: E402
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
from defined_quant.validation import preflight  # noqa: E402

__version__ = "0.1.0"

__all__ = [
    "__version__",
    "DashboardSpec",
    "DiscoveryFilters",
    "FacetValue",
    "FieldMatch",
    "SearchHit",
    "SearchResults",
    "RenderTarget",
    "TableColumn",
    "TableRow",
    "TableSpec",
    "ViewBundleSpec",
    "catalog_facets",
    "component_models",
    "component_record",
    "component_facets",
    "iter_components",
    "load_component",
    "preflight",
    "dashboard_hash",
    "render_dashboard_html",
    "render_dashboard_svg",
    "render_svg",
    "save_dashboard_html",
    "save_dashboard_svg",
    "save_svg",
    "search_components",
    "subject_hash",
    "subject_manifest",
    "select_view",
    "tokenize",
    "view_hash",
    "visualization_hash",
]

del _candidate, _entry
