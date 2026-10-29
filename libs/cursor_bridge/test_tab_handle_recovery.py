"""Tab handle recovery — composer resolve, wake ledger, TAB_READY re-handshake."""

from __future__ import annotations

import json
from pathlib import Path

from cursor_bridge.glass_services_js import (
    RESOLVE_COMPOSER_FOR_AGENT_FN,
    rename_composer_title_js,
    submit_wake_line_js,
)
from cursor_bridge.spawn_endpoint_house_ledger import (
    last_successful_wake,
    record_tab_wake,
)
from cursor_bridge.tab_ready_rehandshake import tab_ready_instruction


def test_submit_wake_line_js_uses_resolve_composer_for_agent() -> None:
    js = submit_wake_line_js("BRIDGE_WAKE thread=1 turn=2", "agent-1")
    assert "resolveComposerForAgent" in js
    assert (
        RESOLVE_COMPOSER_FOR_AGENT_FN.strip() in js or "resolveComposerForAgent" in js
    )
    assert "setActiveAgentId" in js
    assert "composerId" in js


def test_rename_js_uses_resolve_composer_and_tab_ready_flag() -> None:
    js = rename_composer_title_js("agent-1", "20993 slug")
    assert "resolveComposerForAgent" in js
    assert "tab_ready_rehandshake" in js


def test_wake_ledger_records_last_success(tmp_path: Path) -> None:
    aid = "32e69e8c-0000-4000-8000-000000000001"
    record_tab_wake(
        gui_host=":8",
        agent_id=aid,
        op="wake_tab",
        ok=False,
        reason="no_composer",
        watch_dir=tmp_path,
    )
    record_tab_wake(
        gui_host=":8",
        agent_id=aid,
        op="wake_tab",
        ok=True,
        reason="submitted",
        watch_dir=tmp_path,
    )
    record_tab_wake(
        gui_host=":8",
        agent_id=aid,
        op="wake_tab",
        ok=False,
        reason="composer_not_empty",
        watch_dir=tmp_path,
    )
    last = last_successful_wake(aid, watch_dir=tmp_path)
    assert last is not None
    assert last["ok"] is True
    assert last["reason"] == "submitted"
    ledger = tmp_path / "cursor-bridge-tab-wake-ledger.jsonl"
    lines = ledger.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 3
    assert json.loads(lines[1])["ok"] is True


def test_tab_ready_instruction_after_rename() -> None:
    text = tab_ready_instruction("20993 composer-impresario-stage11")
    assert "TAB_READY" in text
    assert "20993 composer-impresario-stage11" in text
