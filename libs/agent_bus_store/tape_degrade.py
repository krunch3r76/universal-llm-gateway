"""Tape budget degrade — pop bodies to index rows and enforce budget_bytes."""

from __future__ import annotations

import json
from typing import Any


class TapeBudgetExceeded(Exception):
    """Raised when no viable pour fits within budget_bytes."""

    def __init__(
        self,
        *,
        thread_id: str,
        budget_bytes: int,
        required_budget_bytes: int,
        min_viable_budget_bytes: int,
    ) -> None:
        self.thread_id = thread_id
        self.budget_bytes = budget_bytes
        self.required_budget_bytes = required_budget_bytes
        self.min_viable_budget_bytes = min_viable_budget_bytes
        super().__init__(
            f"Tape budget {budget_bytes} cannot fit minimum viable pour "
            f"(need {min_viable_budget_bytes} for newest body only, "
            f"{required_budget_bytes} for full messages)"
        )


def tape_budget_exceeded_envelope(exc: TapeBudgetExceeded) -> dict[str, Any]:
    """Structured 413 detail for tape budget overflow (fail closed)."""
    req = exc.required_budget_bytes
    tid = exc.thread_id
    return {
        "error": "tape_budget_exceeded",
        "thread_id": tid,
        "budget_bytes": exc.budget_bytes,
        "required_budget_bytes": req,
        "min_viable_budget_bytes": exc.min_viable_budget_bytes,
        "overflow_uri": f"/threads/{tid}/tape?budget_bytes={req}",
    }


def payload_bytes(
    messages: list[dict[str, Any]], index_rows: list[dict[str, Any]]
) -> int:
    return len(json.dumps({"messages": messages, "index": index_rows}).encode("utf-8"))


def overflow_uri(thread_id: str, required_budget_bytes: int) -> str:
    return f"/threads/{thread_id}/tape?budget_bytes={required_budget_bytes}"


def _bus_turn_id_for_turn(
    cells: list[dict[str, Any]],
    *,
    transcript_id: str,
    turn_index: int,
) -> int | None:
    for cell in cells:
        if str(cell.get("transcript_id") or "") != transcript_id:
            continue
        turn_lo = int(cell.get("turn_lo") or 0)
        turn_hi = int(cell.get("turn_hi") or 0)
        if turn_lo < turn_index <= turn_hi:
            bus_turn_id = cell.get("bus_turn_id")
            return int(bus_turn_id) if bus_turn_id is not None else None
    return None


def build_degraded_basis(
    *,
    thread_id: str,
    bodies_kept: int,
    bodies_dropped: int,
    index_rows: int,
    index_dropped: int,
    budget_bytes: int,
    payload_bytes_val: int,
    required_budget_bytes: int,
) -> dict[str, Any]:
    return {
        "kind": "budget_overflow",
        "bodies_kept": bodies_kept,
        "bodies_dropped": bodies_dropped,
        "index_rows": index_rows,
        "index_dropped": index_dropped,
        "budget_bytes": budget_bytes,
        "payload_bytes": payload_bytes_val,
        "required_budget_bytes": required_budget_bytes,
        "overflow_uri": overflow_uri(thread_id, required_budget_bytes),
    }


def degrade_overflow_messages(
    messages: list[dict[str, Any]],
    *,
    cells: list[dict[str, Any]],
    budget_bytes: int,
    thread_id: str,
) -> tuple[
    list[dict[str, Any]],
    list[dict[str, Any]],
    bool,
    dict[str, Any] | None,
]:
    """Degrade oldest message bodies into index rows; enforce budget or fail closed."""
    original = list(messages)
    original_count = len(original)
    required_budget_bytes = payload_bytes(original, [])

    if required_budget_bytes <= budget_bytes:
        return original, [], False, None

    kept = list(original)
    index_rows: list[dict[str, Any]] = []
    bodies_dropped = 0

    while len(kept) > 1:
        dropped = kept.pop(0)
        bodies_dropped += 1
        sid = str(dropped.get("session_id") or "")
        turn_index = int(dropped.get("turn_index") or 0)
        tid = str(dropped.get("transcript_id") or "")
        index_rows.append(
            {
                "transcript_span": f"transcript:{sid}#turn-{turn_index}",
                "bus_turn_id": _bus_turn_id_for_turn(
                    cells, transcript_id=tid, turn_index=turn_index
                ),
                "session_id": sid,
                "transcript_id": tid,
                "turn_index": turn_index,
            }
        )
        if payload_bytes(kept, index_rows) <= budget_bytes:
            break

    index_dropped = 0
    while index_rows and payload_bytes(kept, index_rows) > budget_bytes:
        index_rows.pop(0)
        index_dropped += 1

    final_payload = payload_bytes(kept, index_rows)
    if final_payload > budget_bytes:
        min_viable = payload_bytes([original[-1]] if original else [], [])
        raise TapeBudgetExceeded(
            thread_id=thread_id,
            budget_bytes=budget_bytes,
            required_budget_bytes=required_budget_bytes,
            min_viable_budget_bytes=min_viable,
        )

    degraded = build_degraded_basis(
        thread_id=thread_id,
        bodies_kept=len(kept),
        bodies_dropped=bodies_dropped,
        index_rows=len(index_rows),
        index_dropped=index_dropped,
        budget_bytes=budget_bytes,
        payload_bytes_val=final_payload,
        required_budget_bytes=required_budget_bytes,
    )
    truncated = (
        bodies_dropped > 0
        or index_dropped > 0
        or len(kept) < original_count
        or final_payload < required_budget_bytes
    )
    return kept, index_rows, truncated, degraded


__all__ = [
    "TapeBudgetExceeded",
    "build_degraded_basis",
    "degrade_overflow_messages",
    "overflow_uri",
    "payload_bytes",
    "tape_budget_exceeded_envelope",
]
