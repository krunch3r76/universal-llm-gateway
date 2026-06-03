"""Regression: search intent=summary must qualify assertion columns (ambiguous id)."""

from __future__ import annotations

from cortex_store.routes.assertions._search import _search_cols
from cortex_store.routes.assertions._shared import _SEARCH_SUMMARY_COLS


def test_search_summary_cols_are_table_qualified() -> None:
    assert _SEARCH_SUMMARY_COLS.startswith("a.")
    assert ", id," not in f", {_SEARCH_SUMMARY_COLS}, "
    cols = _search_cols(intent="summary")
    assert "a.id" in cols
    assert " e.name AS entity_name" in cols
