"""Assertions route package — list / search / create / update / supersede /
entrenchment / enrich.

Split from a single 1452-line module to satisfy the [quality:sloc] ≤400
per-file budget. Each submodule registers its handler(s) on the shared
``router`` defined in ``_shared``. Importing any submodule has the side
effect of registering its routes — so this ``__init__`` imports every
route module at package-load time.

Public surface preserved exactly: ``router``, the dispatch entry points
(``_create_assertion_impl``, ``_list_assertions_impl``, etc.), the route
functions used by callers (``update_assertion``), and the private constants
used by sibling routes (``_ASSERTION_COLS``, ``_JSON_FIELDS``,
``_payload_validation_exception``, ``_sanitize_fts_query``). All historic
``from cortex_store.routes.assertions import X`` imports continue to work.
"""

from __future__ import annotations

# Side-effect imports — registering route handlers on `router`.
from . import _create, _enrich, _entrenchment, _list, _search, _supersede, _update
from ._create import _create_assertion_impl, create_assertion
from ._enrich import enrich_assertion_endpoint
from ._entrenchment import list_assertions_by_entrenchment
from ._list import list_assertions
from ._search import _sanitize_fts_query, search_assertions
from ._shared import (
    _ASSERTION_COLS,
    _ASSERTION_COMPACT_COLS,
    _JSON_FIELDS,
    _SESSION_TAG_RE,
    _VALID_CONFIDENCE,
    _VALID_REVIEW_STATUS,
    _embed_assertion_background,
    _log_search_access,
    _payload_validation_exception,
    router,
)
from ._supersede import _supersede_assertion_impl, supersede_assertion
from ._update import _update_assertion_impl, update_assertion


def _list_assertions_impl(**kwargs: object) -> dict[str, object]:
    """Dispatch-layer wrapper around the ``list_assertions`` route."""
    data = list_assertions(**kwargs)  # type: ignore[arg-type]
    return data.model_dump(mode="json")


def _search_assertions_impl(**kwargs: object) -> dict[str, object]:
    """Dispatch-layer wrapper around the ``search_assertions`` route."""
    data = search_assertions(**kwargs)  # type: ignore[arg-type]
    return data.model_dump(mode="json")


__all__ = [
    "_ASSERTION_COLS",
    "_ASSERTION_COMPACT_COLS",
    "_JSON_FIELDS",
    "_SESSION_TAG_RE",
    "_VALID_CONFIDENCE",
    "_VALID_REVIEW_STATUS",
    "_create_assertion_impl",
    "_embed_assertion_background",
    "_list_assertions_impl",
    "_log_search_access",
    "_payload_validation_exception",
    "_sanitize_fts_query",
    "_search_assertions_impl",
    "_supersede_assertion_impl",
    "_update_assertion_impl",
    "create_assertion",
    "enrich_assertion_endpoint",
    "list_assertions",
    "list_assertions_by_entrenchment",
    "router",
    "search_assertions",
    "supersede_assertion",
    "update_assertion",
]

# Keep the side-effect imports referenced so static analysers don't flag
# them as unused — registration via decorator runs at import time.
_ = (_create, _enrich, _entrenchment, _list, _search, _supersede, _update)
