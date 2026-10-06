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


def test_framing_roundtrip_one_mib_payload() -> None:
    big = "x" * (1024 * 1024)
    payload = _tools_call_response(text=big)
    buf = io.BytesIO()
    write_framed_message(buf, payload)
    buf.seek(0)
    roundtrip = read_framed_message(buf)
    assert roundtrip == payload
    assert roundtrip["result"]["content"][0]["text"] == big
