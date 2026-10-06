"""Hermetic tests for bridge-side steer spool helpers."""

from __future__ import annotations

import io
import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from scripts.mcp_bridge_contract_filter import (  # noqa: E402
    _copy_upstream,
    _maybe_inject_steer,
    read_framed_message,
    write_framed_message,
)
from scripts.mcp_bridge_steer_inject import (  # noqa: E402
    CURSOR_SDK_DISPATCH_ID_ENV,
    STEER_LIVE_LEDGER_STATUSES,
    STEER_TERMINAL_LEDGER_STATUSES,
    ULG_STEER_SPOOL_DIR_ENV,
    PendingSteer,
    append_directive,
    append_spool_entry,
    claim_pending,
    consume_next_steer_envelope,
    mark_delivered,
    native_tool_steer_hook_response,
    spool_path,
)


def _tools_call_response(*, text: str = '{"ok":true}') -> dict:
    return {
        "jsonrpc": "2.0",
        "id": 42,
        "result": {
            "content": [{"type": "text", "text": text}],
            "isError": False,
        },
    }


def test_append_directive_preserves_content_zero() -> None:
    payload = _tools_call_response(text='{"before":1}')
    pending = PendingSteer(
        entry_id="e1",
        dispatch_id="disp-1",
        authority_turn_id="7",
        directive="wrong service",
        deposited_at="2026-09-13T00:00:00+00:00",
        ttl_s=300,
    )
    out = append_directive(payload, pending)
    assert out["result"]["content"][0]["text"] == '{"before":1}'
    assert len(out["result"]["content"]) == 2
    assert "wrong service" in out["result"]["content"][1]["text"]


def test_claim_and_mark_delivered_roundtrip(tmp_path: Path) -> None:
    append_spool_entry(
        "disp-a",
        authority_turn_id="11",
        directive="steer text",
        ttl_s=300,
        spool_dir=tmp_path,
        entry_id="entry-1",
    )
    claimed = claim_pending("disp-a", spool_dir=tmp_path)
    assert claimed is not None
    assert claimed.entry_id == "entry-1"
    assert claim_pending("disp-a", spool_dir=tmp_path) is None
    mark_delivered(claimed, spool_dir=tmp_path)
    from scripts.mcp_bridge_steer_inject import read_delivery_ack

    ack = read_delivery_ack("disp-a", "entry-1", spool_dir=tmp_path)
    assert ack is not None
    assert ack.get("delivered_at")


def test_maybe_inject_skips_tools_list() -> None:
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "result": {"tools": [{"name": "cortex"}]},
    }
    out = _maybe_inject_steer(payload, {1: "tools/list"})
    assert out == payload


def test_maybe_inject_skips_is_error() -> None:
    payload = {
        "jsonrpc": "2.0",
        "id": 2,
        "result": {"content": [{"type": "text", "text": "err"}], "isError": True},
    }
    out = _maybe_inject_steer(payload, {2: "tools/call"})
    assert out == payload


def test_copy_upstream_injects_on_tools_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    append_spool_entry(
        "disp-inject",
        authority_turn_id="3",
        directive="use Stargate",
        ttl_s=300,
        spool_dir=tmp_path,
        entry_id="e-inject",
    )
    monkeypatch.setenv(CURSOR_SDK_DISPATCH_ID_ENV, "disp-inject")
    monkeypatch.setenv(ULG_STEER_SPOOL_DIR_ENV, str(tmp_path))
    payload = _tools_call_response()
    upstream = io.BytesIO()
    write_framed_message(upstream, payload)
    upstream.seek(0)
    downstream = io.BytesIO()
    _copy_upstream(upstream, downstream, allow=None, pending_methods={42: "tools/call"})
    downstream.seek(0)
    relayed = read_framed_message(downstream)
    assert relayed is not None
    assert len(relayed["result"]["content"]) == 2
    assert "use Stargate" in relayed["result"]["content"][1]["text"]


def _age_pending(spool: Path, dispatch_id: str) -> None:
    path = spool_path(spool, dispatch_id)
    data = json.loads(path.read_text(encoding="utf-8"))
    for raw in data["pending"]:
        raw["deposited_at"] = "2020-01-01T00:00:00+00:00"
        raw["ttl_s"] = 1
    path.write_text(json.dumps(data), encoding="utf-8")


def _deposit(spool: Path, dispatch_id: str, entry_id: str, directive: str) -> None:
    append_spool_entry(
        dispatch_id,
        authority_turn_id="9",
        directive=directive,
        ttl_s=300,
        spool_dir=spool,
        entry_id=entry_id,
    )


def test_b1_native_hook_delivers_without_mcp(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _deposit(tmp_path, "disp-native", "e-native", "operator ruling")
    monkeypatch.setenv(CURSOR_SDK_DISPATCH_ID_ENV, "disp-native")
    monkeypatch.setenv(ULG_STEER_SPOOL_DIR_ENV, str(tmp_path))
    monkeypatch.setattr(
        "scripts.mcp_bridge_steer_inject.steer_dispatch_is_live",
        lambda _dispatch_id: True,
    )
    response = native_tool_steer_hook_response({"tool_name": "Shell"})
    assert "operator ruling" in response["additional_context"]
    assert response["additional_context"].startswith("ULG_STEER:")


def test_b2_mcp_name_leaves_steer_for_the_bridge(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _deposit(tmp_path, "disp-mcp", "e-mcp", "use Stargate")
    monkeypatch.setenv(CURSOR_SDK_DISPATCH_ID_ENV, "disp-mcp")
    monkeypatch.setenv(ULG_STEER_SPOOL_DIR_ENV, str(tmp_path))
    monkeypatch.setattr(
        "scripts.mcp_bridge_steer_inject.steer_dispatch_is_live",
        lambda _dispatch_id: True,
    )
    assert (
        native_tool_steer_hook_response({"tool_name": "MCP:vortex-code-cortex"}) == {}
    )
    payload = _tools_call_response()
    upstream = io.BytesIO()
    write_framed_message(upstream, payload)
    upstream.seek(0)
    downstream = io.BytesIO()
    _copy_upstream(upstream, downstream, allow=None, pending_methods={42: "tools/call"})
    downstream.seek(0)
    relayed = read_framed_message(downstream)
    assert relayed is not None
    assert "use Stargate" in relayed["result"]["content"][1]["text"]
    again = io.BytesIO()
    write_framed_message(again, _tools_call_response())
    again.seek(0)
    second = io.BytesIO()
    _copy_upstream(again, second, allow=None, pending_methods={42: "tools/call"})
    second.seek(0)
    relayed_again = read_framed_message(second)
    assert relayed_again is not None
    assert len(relayed_again["result"]["content"]) == 1


def test_b3_fifo_on_successive_native_tools(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _deposit(tmp_path, "disp-fifo", "e-first", "first ruling")
    _deposit(tmp_path, "disp-fifo", "e-second", "second ruling")
    monkeypatch.setenv(CURSOR_SDK_DISPATCH_ID_ENV, "disp-fifo")
    monkeypatch.setenv(ULG_STEER_SPOOL_DIR_ENV, str(tmp_path))
    monkeypatch.setattr(
        "scripts.mcp_bridge_steer_inject.steer_dispatch_is_live",
        lambda _dispatch_id: True,
    )
    first = native_tool_steer_hook_response({"tool_name": "Read"})
    second = native_tool_steer_hook_response({"tool_name": "Grep"})
    assert "first ruling" in first["additional_context"]
    assert "second ruling" in second["additional_context"]
    assert "e-first" in first["additional_context"]
    assert "e-second" in second["additional_context"]


def test_b4_ttl_elapsed_while_live_still_delivers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _deposit(tmp_path, "disp-ttl", "e-ttl", "late ruling")
    _age_pending(tmp_path, "disp-ttl")
    assert claim_pending("disp-ttl", spool_dir=tmp_path) is None
    monkeypatch.setenv(CURSOR_SDK_DISPATCH_ID_ENV, "disp-ttl")
    monkeypatch.setenv(ULG_STEER_SPOOL_DIR_ENV, str(tmp_path))
    monkeypatch.setattr(
        "scripts.mcp_bridge_steer_inject.steer_dispatch_is_live",
        lambda _dispatch_id: True,
    )
    response = native_tool_steer_hook_response({"tool_name": "Shell"})
    assert "late ruling" in response["additional_context"]


def test_b5_delivered_once_not_on_later_tool(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _deposit(tmp_path, "disp-once", "e-once", "only once")
    monkeypatch.setenv(CURSOR_SDK_DISPATCH_ID_ENV, "disp-once")
    monkeypatch.setenv(ULG_STEER_SPOOL_DIR_ENV, str(tmp_path))
    monkeypatch.setattr(
        "scripts.mcp_bridge_steer_inject.steer_dispatch_is_live",
        lambda _dispatch_id: True,
    )
    assert (
        "only once"
        in native_tool_steer_hook_response({"tool_name": "Shell"})["additional_context"]
    )
    assert native_tool_steer_hook_response({"tool_name": "Grep"}) == {}
    assert consume_next_steer_envelope("disp-once", spool_dir=tmp_path) is None


def test_consume_refuses_when_dispatch_has_ended(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _deposit(tmp_path, "disp-ended", "e-ended", "too late")
    monkeypatch.setattr(
        "scripts.mcp_bridge_steer_inject.steer_dispatch_is_live",
        lambda _dispatch_id: False,
    )
    assert consume_next_steer_envelope("disp-ended", spool_dir=tmp_path) is None
    assert claim_pending("disp-ended", spool_dir=tmp_path) is not None


def test_steer_live_statuses_match_ledger() -> None:
    from services.git_integration_worker.cursor_dispatch_ledger import (
        _STATUS_TERMINAL,
    )

    assert STEER_TERMINAL_LEDGER_STATUSES == frozenset(_STATUS_TERMINAL)
    assert STEER_LIVE_LEDGER_STATUSES == frozenset(
        {"admitted", "running", "parked_waiting"}
    )
    assert "queued" not in STEER_LIVE_LEDGER_STATUSES


def test_native_hook_records_delivered_via(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Delivery is recorded before the model reads additional_context."""
    _deposit(tmp_path, "disp-via", "e-via", "via ruling")
    monkeypatch.setenv(CURSOR_SDK_DISPATCH_ID_ENV, "disp-via")
    monkeypatch.setenv(ULG_STEER_SPOOL_DIR_ENV, str(tmp_path))
    monkeypatch.setattr(
        "scripts.mcp_bridge_steer_inject.steer_dispatch_is_live",
        lambda _dispatch_id: True,
    )
    response = native_tool_steer_hook_response({"tool_name": "Shell"})
    assert "via ruling" in response["additional_context"]
    from scripts.mcp_bridge_steer_inject import read_delivery_ack

    ack = read_delivery_ack("disp-via", "e-via", spool_dir=tmp_path)
    assert ack is not None
    assert ack.get("delivered_via") == "native_hook"


def test_empty_spool_dir_env_does_not_resolve_cwd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Blank ULG_STEER_SPOOL_DIR is absent; consume does not read '.'."""
    monkeypatch.setenv(ULG_STEER_SPOOL_DIR_ENV, "  ")
    assert consume_next_steer_envelope("disp-empty") is None


def test_append_spool_entry_concurrent_keeps_both(tmp_path: Path) -> None:
    """Two producers under the spool lock do not drop a row."""
    import threading

    errors: list[BaseException] = []

    def _deposit(entry_id: str) -> None:
        try:
            append_spool_entry(
                "disp-race",
                authority_turn_id="1",
                directive=entry_id,
                ttl_s=300,
                spool_dir=tmp_path,
                entry_id=entry_id,
            )
        except BaseException as exc:  # noqa: BLE001 — test collects thread faults
            errors.append(exc)

    threads = [threading.Thread(target=_deposit, args=(f"e-{i}",)) for i in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert errors == []
    data = json.loads(spool_path(tmp_path, "disp-race").read_text(encoding="utf-8"))
    assert {row["entry_id"] for row in data["pending"]} == {f"e-{i}" for i in range(8)}
    assert list(tmp_path.glob("*.tmp")) == []


def test_ledger_read_uses_env_sqlite(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Liveness reads CURSOR_SDK_DISPATCH_LEDGER with sqlite3, not a GIW import."""
    import sqlite3

    from scripts.mcp_bridge_steer_inject import (
        CURSOR_SDK_DISPATCH_LEDGER_ENV,
        steer_dispatch_is_live,
    )

    db = tmp_path / "ledger.db"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE cursor_sdk_dispatches (dispatch_id TEXT, status TEXT)")
    conn.execute(
        "INSERT INTO cursor_sdk_dispatches VALUES (?, ?)",
        ("disp-live", "running"),
    )
    conn.execute(
        "INSERT INTO cursor_sdk_dispatches VALUES (?, ?)",
        ("disp-done", "completed"),
    )
    conn.commit()
    conn.close()
    monkeypatch.setenv(CURSOR_SDK_DISPATCH_LEDGER_ENV, str(db))
    assert steer_dispatch_is_live("disp-live") is True
    assert steer_dispatch_is_live("disp-done") is False
    monkeypatch.setenv(CURSOR_SDK_DISPATCH_LEDGER_ENV, "")
    assert steer_dispatch_is_live("disp-live") is None


def test_hook_lock_busy_returns_empty(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """LOCK_NB exhaustion returns {} and leaves the row pending."""
    import fcntl

    _deposit(tmp_path, "disp-busy", "e-busy", "held")
    monkeypatch.setenv(CURSOR_SDK_DISPATCH_ID_ENV, "disp-busy")
    monkeypatch.setenv(ULG_STEER_SPOOL_DIR_ENV, str(tmp_path))
    monkeypatch.setattr(
        "scripts.mcp_bridge_steer_inject.steer_dispatch_is_live",
        lambda _dispatch_id: True,
    )
    monkeypatch.setattr(
        "scripts.mcp_bridge_steer_inject._LOCK_NB_ATTEMPTS",
        1,
    )
    monkeypatch.setattr(
        "scripts.mcp_bridge_steer_inject._LOCK_NB_SLEEP_S",
        0,
    )
    path = spool_path(tmp_path, "disp-busy")
    lock_path = path.with_suffix(path.suffix + ".lock")
    handle = lock_path.open("a+")
    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    try:
        assert native_tool_steer_hook_response({"tool_name": "Shell"}) == {}
    finally:
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        handle.close()
    still = claim_pending("disp-busy", spool_dir=tmp_path)
    assert still is not None
    assert still.entry_id == "e-busy"


def test_hook_main_fail_open_on_import_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """Import failure inside main writes {}."""
    import scripts.mcp_bridge_steer_hook as hook

    real_import = __import__

    def _guarded(name, *args, **kwargs):
        if name == "scripts.mcp_bridge_steer_inject":
            raise ImportError("steer inject missing")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr("builtins.__import__", _guarded)
    buf = io.StringIO()
    monkeypatch.setattr(sys, "stdin", io.StringIO("{}"))
    monkeypatch.setattr(sys, "stdout", buf)
    hook.main()
    assert buf.getvalue() == "{}"


def test_framing_roundtrip_one_mib_payload() -> None:
    big = "x" * (1024 * 1024)
    payload = _tools_call_response(text=big)
    buf = io.BytesIO()
    write_framed_message(buf, payload)
    buf.seek(0)
    roundtrip = read_framed_message(buf)
    assert roundtrip == payload
    assert roundtrip["result"]["content"][0]["text"] == big
