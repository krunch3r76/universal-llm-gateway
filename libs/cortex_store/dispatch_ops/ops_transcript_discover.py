"""Transcript discover dispatch op — idle window scan at resume."""

from __future__ import annotations

import os
from datetime import UTC, datetime
from typing import Any

from agent_bus_store.thread_classification import classify_thread
from transport_utils import DEFAULT_AGENT_BUS_URL, make_sync_client
from universal_logging import get_logger

from ..events_tape import transcript_discover_filtered
from ..session_close_successor_hop import (
    conversation_uuid_from_jsonl_path,
    lookup_sealed_journal,
)
from ..transcript_assembly import _transcripts_root
from ..transcript_lane_touch import binding_for, lane_touches
from ..transcript_session_id import _jsonl_paths_by_mtime_desc

logger = get_logger("cortex-api.dispatch_ops.transcript_discover")


def _thread_detail(thread_id: str) -> dict[str, Any] | None:
    token = os.environ.get("AGENT_BUS_TOKEN", "").strip()
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    try:
        with make_sync_client(DEFAULT_AGENT_BUS_URL, timeout=5.0) as client:
            resp = client.get(f"/threads/{thread_id}", headers=headers)
            if resp.status_code != 200:
                return None
            data = resp.json()
    except Exception:
        return None
    return data if isinstance(data, dict) else None


def _parse_created_at(raw: str | None) -> datetime | None:
    if not raw:
        return None
    text = raw.strip()
    iso = text[:-1] + "+00:00" if text.endswith("Z") else text
    try:
        parsed = datetime.fromisoformat(iso)
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=UTC)
        return parsed.astimezone(UTC)
    except ValueError:
        return None


from ..transcript_cp_anchors import explicit_uuids_for_lane


def _resolve_explicit_uuids(
    thread_id: str,
    explicit_transcript_ids: list[str] | None,
) -> set[str]:
    """Merge CP anchors at the op boundary unless the caller already did."""
    explicit = {x.strip() for x in (explicit_transcript_ids or []) if x and x.strip()}
    if explicit_transcript_ids is not None:
        return explicit
    return explicit_uuids_for_lane(thread_id, explicit)


def _discover_open_windows(
    *,
    thread_id: str,
    lane_created_at: datetime,
    explicit_uuids: set[str] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, int]]:
    root = _transcripts_root()
    open_windows: list[dict[str, Any]] = []
    excluded: list[dict[str, Any]] = []
    excluded_counts = {
        "read_only": 0,
        "foreign_dominant": 0,
        "no_touch": 0,
        "dropped": 0,
        "segment_unavailable": 0,
    }
    explicit_all = explicit_uuids or set()
    for jsonl_path in _jsonl_paths_by_mtime_desc(root):
        mtime = datetime.fromtimestamp(jsonl_path.stat().st_mtime, tz=UTC)
        if mtime < lane_created_at:
            continue
        uuid = conversation_uuid_from_jsonl_path(jsonl_path)
        rel = str(jsonl_path.relative_to(root))
        from ..transcript_session_id import derive_session_id_from_jsonl_start

        session_id = derive_session_id_from_jsonl_start(
            jsonl_path=jsonl_path, agent="cursor"
        )
        if session_id and lookup_sealed_journal(session_id) is not None:
            continue
        touches = lane_touches(jsonl_path)
        binding, dominant = binding_for(
            thread_id,
            touches,
            explicit_uuids=explicit_all,
            conversation_uuid=uuid,
        )
        lane_touch = touches.get(thread_id)
        write_touches = lane_touch.writes if lane_touch else 0
        row = {
            "transcript_id": uuid,
            "jsonl_path": rel,
            "session_id": session_id,
            "conversation_uuid": uuid,
            "binding": binding,
            "dominant_lane": dominant,
            "write_touches": write_touches,
            "mtime": mtime.isoformat(),
        }
        if binding in {"explicit_cp", "dominant_write"}:
            open_windows.append(row)
            continue
        if binding == "read_only":
            excluded_counts["read_only"] += 1
        elif binding == "foreign_dominant":
            excluded_counts["foreign_dominant"] += 1
        elif binding == "no_touch":
            excluded_counts["no_touch"] += 1
        else:
            excluded_counts["dropped"] += 1
        excluded.append({**row, "reason": binding})
        transcript_discover_filtered(reason=binding, thread_id=thread_id, transcript_id=uuid)
    return open_windows, excluded, excluded_counts


def _op_transcript_discover(
    thread: str | None = None,
    thread_id: str | None = None,
    explicit_transcript_ids: list[str] | None = None,
    **_: object,
) -> dict[str, Any]:
    """Discover idle JSONL windows for a continuity lane (read-only scan).

    Zero durable writes — seal happens only via ``transcript_seal``.
    """
    tid = thread or thread_id
    if not tid:
        return {"error": "thread is required", "reason": "missing_arg", "code": "tape.missing_thread"}

    detail = _thread_detail(str(tid))
    if detail is None:
        return {
            "error": f"thread {tid!r} not found",
            "reason": "thread_not_found",
            "code": "tape.thread_not_found",
        }

    tags = detail.get("tags") or []
    classification = classify_thread(tags)
    if classification["spine"] != "root":
        return {
            "error": f"thread {tid} is not a root continuity lane",
            "reason": "not_root",
            "code": "tape.not_root",
        }

    created_at = _parse_created_at(detail.get("created_at"))
    if created_at is None:
        created_at = datetime.min.replace(tzinfo=UTC)

    explicit_all = _resolve_explicit_uuids(str(tid), explicit_transcript_ids)
    open_windows, excluded, excluded_counts = _discover_open_windows(
        thread_id=str(tid),
        lane_created_at=created_at,
        explicit_uuids=explicit_all,
    )
    return {
        "thread_id": str(tid),
        "open_windows": open_windows,
        "open_window_count": len(open_windows),
        "excluded": excluded,
        "excluded_counts": excluded_counts,
    }


__all__ = ["_discover_open_windows", "_op_transcript_discover", "_resolve_explicit_uuids"]
