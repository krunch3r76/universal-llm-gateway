"""Binding store — target_id lookup and CDP key normalization."""

from __future__ import annotations

from pathlib import Path

from web_tab.tab_binding_store import (
    load_binding_for_target,
    save_binding,
)
from web_tab.tab_session import tab_session_from_row


def test_load_binding_for_target_matches_normalized_cdp(tmp_path: Path) -> None:
    store = tmp_path / "bindings.json"
    session = tab_session_from_row(
        target_id="tid-1",
        cdp_url="http://127.0.0.1:9342",
        conversation_url="https://grok.com/",
        surface="grok",
        created_by_agent=True,
    )
    save_binding(session, path=store)
    found = load_binding_for_target(
        cdp_url="http://localhost:9342",
        target_id="tid-1",
        path=store,
    )
    assert found is not None
    assert found.created_by_agent is True
    assert found.target_id == "tid-1"
