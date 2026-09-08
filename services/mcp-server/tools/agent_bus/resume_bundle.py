"""Resume substrate envelope — verbal tape plus projection pointers.

Read-only. Harvests and renders tape; does not read the graph, dispatch
consolidation, or compute projections (decision:continuity-resume-stack-
adjudication-2026-09-08).
"""

from __future__ import annotations

from typing import Any

from ._shared import _structured_relay_error, relay

_MECHANICAL_PROJECTION_URI = (
    "cortex://notes/system/threads/{thread}-transcript-projection.md"
)


def _tape_verbal_from_render(tape: dict[str, Any]) -> list[Any]:
    """Use W1 ``verbal_messages``; else strip mechanical extras to {role, content}."""
    verbal = tape.get("verbal_messages")
    if isinstance(verbal, list):
        return verbal
    return [
        {"role": msg.get("role"), "content": msg.get("content")}
        for msg in (tape.get("messages") or [])
        if isinstance(msg, dict)
    ]


def _harvested_tape(thread_id: str) -> dict[str, Any]:
    """Relay tape render — MCP process lacks agent-bus sqlite (same as ``tape`` op)."""
    result = relay(
        "agent-bus",
        "GET",
        f"/threads/{thread_id}/tape?harvest=true&format=verbal",
    )
    if isinstance(result, dict) and "error" in result:
        structured = _structured_relay_error(result, op="resume_bundle")
        if structured is not None:
            return structured
        return {"error": f"agent-bus error: {result['error']}"}
    return result if isinstance(result, dict) else {}


def _resume_bundle_dispatch(
    *,
    thread: str | int = "",
    thread_id: str | int = "",
) -> dict[str, Any]:
    """Thin resume envelope: verbal tape + pointer URIs (no graph)."""
    lane = str(thread or thread_id or "")
    if not lane:
        return {"error": "resume_bundle requires: thread", "reason": "missing_arg"}
    tape = _harvested_tape(lane)
    if tape.get("error"):
        return tape
    return {
        "tape_verbal": _tape_verbal_from_render(tape),
        "mechanical_projection_uri": _MECHANICAL_PROJECTION_URI.format(thread=lane),
        "word_projection_uri": None,
        "consolidate_summary_row": None,
    }
