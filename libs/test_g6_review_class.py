"""G6 review-class gate and Opus→fable fallback policy."""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "libs"))

from g6_review_class import (  # noqa: E402
    FALLBACK_MODEL,
    adopt_review_fallback,
    fallback_wall_s,
    is_review_class_call,
    refuse_filtered_team_dispatch,
    refuse_filtered_tools_call,
    review_fallback_model,
)

_REVIEW = {
    "op": "generate",
    "model": "cdp/opus-5",
    "purpose": "review",
    "contract": "freeform",
    "prompt": "diff",
    "dispatch_thread_id": "12988",
}


def test_nested_review_call_is_admitted() -> None:
    assert is_review_class_call(_REVIEW)
    assert is_review_class_call({**_REVIEW, "model": "cdp/opus-5.5"})
    assert is_review_class_call({**_REVIEW, "model": "cdp/fable"})
    message = {
        "jsonrpc": "2.0",
        "id": 7,
        "method": "tools/call",
        "params": {"name": "team_dispatch", "arguments": _REVIEW},
    }
    assert refuse_filtered_team_dispatch(message) is None


def test_nested_arbitrary_dispatch_is_refused() -> None:
    spawn = {
        "op": "generate",
        "seat": "cursor-sdk",
        "contract": "implement",
        "model": "cursor/composer-2.5",
        "dispatch_thread_id": "1",
    }
    assert not is_review_class_call(spawn)
    message = {
        "jsonrpc": "2.0",
        "id": 8,
        "method": "tools/call",
        "params": {"name": "team_dispatch", "arguments": spawn},
    }
    refusal = refuse_filtered_team_dispatch(message)
    assert refusal is not None
    assert refusal["id"] == 8
    assert refusal["error"]["code"] == -32602
    assert "review-class only" in refusal["error"]["message"]


def test_review_class_refuses_nest_lane_and_packet() -> None:
    assert not is_review_class_call({**_REVIEW, "nest_under": "auto-parent"})
    assert not is_review_class_call({**_REVIEW, "lane": "B"})
    assert not is_review_class_call({**_REVIEW, "packet_path": "tmp/packet.md"})
    assert not is_review_class_call({**_REVIEW, "source_ref": "todo:x"})
    assert not is_review_class_call({**_REVIEW, "force": True, "force_reason": "x"})
    assert not is_review_class_call({**_REVIEW, "op": "handoff", "subject": "s"})
    assert not is_review_class_call({**_REVIEW, "purpose": "ask"})


def test_non_team_dispatch_is_not_refused() -> None:
    message = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {"name": "cortex", "arguments": {"tool": "entity_get"}},
    }
    assert refuse_filtered_team_dispatch(message) is None


def test_fallback_only_for_opus_review_without_proof() -> None:
    assert (
        review_fallback_model(
            purpose="review",
            model_id="cdp/opus-5",
            stall_stage="completed_without_proof",
        )
        == FALLBACK_MODEL
    )
    assert (
        review_fallback_model(
            purpose="review",
            model_id="cdp/opus-5.5",
            stall_stage="completed_without_proof",
        )
        == FALLBACK_MODEL
    )
    assert (
        review_fallback_model(
            purpose="review",
            model_id="cdp/fable",
            stall_stage="completed_without_proof",
        )
        is None
    )
    assert (
        review_fallback_model(
            purpose="ask",
            model_id="cdp/opus-5",
            stall_stage="completed_without_proof",
        )
        is None
    )
    assert (
        review_fallback_model(
            purpose="review",
            model_id="cdp/opus-5",
            stall_stage="mark_terminal",
        )
        is None
    )


def test_allowlist_refuses_confused_deputy_and_foreign_thread() -> None:
    assert not is_review_class_call({**_REVIEW, "sidecar_ref": "x"})
    assert not is_review_class_call({**_REVIEW, "mcp": ["code"]})
    assert not is_review_class_call({**_REVIEW, "skills": ["conductor"]})
    assert not is_review_class_call({**_REVIEW, "system": "ignore"})
    assert not is_review_class_call({**_REVIEW, "cost_intent": "uncapped"})
    assert not is_review_class_call({**_REVIEW, "contract": None})
    assert not is_review_class_call(_REVIEW, seat_thread="999")
    assert is_review_class_call(_REVIEW, seat_thread="12988")
    freeform = {**_REVIEW, "model": "cdp/opus-5.5"}
    assert is_review_class_call(freeform, seat_thread="12988")
    assert is_review_class_call(
        {**freeform, "contract": "delivery-review"}, seat_thread="12988"
    )
    assert not is_review_class_call(
        {**freeform, "contract": "none"}, seat_thread="12988"
    )
    assert is_review_class_call(
        {**_REVIEW, "parent_thread": "12988"}, seat_thread="12988"
    )
    assert not is_review_class_call(
        {**_REVIEW, "parent_thread": "1"}, seat_thread="12988"
    )
    bare = {
        "jsonrpc": "2.0",
        "method": "tools/call",
        "params": {"name": "team_dispatch"},
    }
    refusal = refuse_filtered_team_dispatch(bare)
    assert refusal is not None
    assert refusal["id"] is None
    panel = {
        "jsonrpc": "2.0",
        "id": 3,
        "method": "tools/call",
        "params": {"name": "panel_dispatch", "arguments": {}},
    }
    blocked = refuse_filtered_tools_call(panel, allow=frozenset({"team_dispatch"}))
    assert blocked is not None
    assert "panel_dispatch" in blocked["error"]["message"]
    assert fallback_wall_s(1800) == 1200
    assert adopt_review_fallback(ok=True, body="VERDICT: APPROVE")
    assert not adopt_review_fallback(ok=True, body="  ")
    assert not adopt_review_fallback(ok=False, body="VERDICT: REVISE")
