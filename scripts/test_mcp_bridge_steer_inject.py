"""Hermetic tests for bridge-side steer spool helpers."""

from __future__ import annotations

import io
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
    ULG_STEER_SPOOL_DIR_ENV,
    PendingSteer,
    append_directive,
    append_spool_entry,
    claim_pending,
    mark_delivered,
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


def test_framing_roundtrip_one_mib_payload() -> None:
    big = "x" * (1024 * 1024)
    payload = _tools_call_response(text=big)
    buf = io.BytesIO()
    write_framed_message(buf, payload)
    buf.seek(0)
    roundtrip = read_framed_message(buf)
    assert roundtrip == payload
    assert roundtrip["result"]["content"][0]["text"] == big
