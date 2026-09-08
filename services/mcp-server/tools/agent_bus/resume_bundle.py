"""Resume substrate envelope — verbal tape plus projection pointers.

Read-only. Harvests and renders tape; does not read the graph, dispatch
consolidation, or compute projections (decision:continuity-resume-stack-
adjudication-2026-09-08).
"""

from __future__ import annotations

from typing import Any

_DEFAULT_TAPE_BUDGET_BYTES = 512_000
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
    from agent_bus_store.tape_harvest import render_tape_with_harvest

    result = render_tape_with_harvest(
        thread_id=thread_id,
        budget_bytes=_DEFAULT_TAPE_BUDGET_BYTES,
        harvest=True,
        format="verbal",
    )
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
