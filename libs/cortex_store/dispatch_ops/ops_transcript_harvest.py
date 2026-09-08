"""Transcript harvest dispatch op — capped discover→seal in cortex-api (D2)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from universal_logging import get_logger

from ..events_tape import transcript_harvested
from ..transcript_assembly import resolve_jsonl_path, TranscriptPathError
from ..transcript_lane_touch import binding_for, lane_touches
from .ops_transcript_discover import _discover_open_windows, _thread_detail, _parse_created_at
from .ops_transcript_seal import _op_transcript_seal

logger = get_logger("cortex-api.dispatch_ops.transcript_harvest")


def _priority_key(window: dict[str, Any]) -> tuple[int, int, str]:
    binding = str(window.get("binding") or "")
    priority = 0 if binding == "explicit_cp" else 1
    write_touches = int(window.get("write_touches") or 0)
    mtime = str(window.get("mtime") or "")
    return (priority, -write_touches, mtime)


def _op_transcript_harvest(
    thread: str | None = None,
    thread_id: str | None = None,
    explicit_transcript_ids: list[str] | None = None,
    max_seals: int = 8,
    **_: object,
) -> dict[str, Any]:
    """Discover bindable windows and seal up to *max_seals* in priority order."""
    tid = thread or thread_id
    if not tid:
        return {
            "error": "thread is required",
            "reason": "missing_arg",
            "code": "transcript_harvest.missing_thread",
        }

    detail = _thread_detail(str(tid))
    if detail is None:
        return {
            "error": f"thread {tid!r} not found",
            "reason": "thread_not_found",
            "code": "transcript_harvest.thread_not_found",
        }

    from agent_bus_store.thread_classification import classify_thread

    if classify_thread(detail.get("tags") or [])["spine"] != "root":
        return {
            "error": f"thread {tid} is not a root continuity lane",
            "reason": "not_root",
            "code": "transcript_harvest.not_root",
        }

    created_at = _parse_created_at(detail.get("created_at"))
    if created_at is None:
        created_at = datetime.min.replace(tzinfo=UTC)

    explicit = {x.strip() for x in (explicit_transcript_ids or []) if x and x.strip()}
    windows, _, _ = _discover_open_windows(
        thread_id=str(tid),
        lane_created_at=created_at,
        explicit_uuids=explicit,
    )
    discovered = len(windows)
    cap = max(0, int(max_seals))
    ordered = sorted(windows, key=_priority_key)
    to_seal = ordered[:cap]
    deferred = ordered[cap:]

    sealed = 0
    refused = 0
    refusal_reasons: list[dict[str, Any]] = []
    for window in to_seal:
        result = _op_transcript_seal(
            thread=str(tid),
            jsonl_path=window.get("jsonl_path"),
            binding=window.get("binding"),
        )
        if result.get("error"):
            code = str(result.get("code") or result.get("reason") or "refused")
            if code == "transcript_seal.already_closed":
                sealed += 1
                continue
            refused += 1
            refusal_reasons.append(
                {
                    "transcript_id": window.get("transcript_id"),
                    "code": code,
                    "error": result.get("error"),
                }
            )
            continue
        sealed += 1

    transcript_harvested(
        thread_id=str(tid),
        discovered=discovered,
        sealed=sealed,
        deferred=len(deferred),
        refused=refused,
    )
    return {
        "thread_id": str(tid),
        "discovered": discovered,
        "sealed": sealed,
        "deferred": [
            {
                "transcript_id": w.get("transcript_id"),
                "jsonl_path": w.get("jsonl_path"),
                "binding": w.get("binding"),
            }
            for w in deferred
        ],
        "deferred_count": len(deferred),
        "refused": refused,
        "refusal_reasons": refusal_reasons,
    }


__all__ = ["_op_transcript_harvest"]
