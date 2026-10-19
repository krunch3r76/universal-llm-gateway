"""Lane closeout journal — structured O→L→N debrief rows on the parent root."""

from __future__ import annotations

import os
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

import httpx

from bus_watch.events import emit_lane_closeout_observed
from bus_watch.lane_closeout_parse import (
    ABANDONED_SUBJECT_PREFIX,
    CLOSEOUT_SUBJECT_PREFIX,
    _find_worker_closeout_turn,
    _parse_json_closeout_envelope,  # noqa: F401 — re-export for tests
    _terminal_status,
    _utcnow_iso,
    build_closeout_record,
    closeout_idempotency_key,
    closeout_needs_friction_file,
    closeout_text_has_assertion_id,
    file_closeout_friction,
    format_closeout_body,
    format_closeout_subject,
    friction_note_from_closeout,
    friction_owner_from_closeout,
    parse_closeout_turn,
    query_lane_closeouts,
)
from bus_watch.lane_live import probe_live
from bus_watch.spawn_pending import row_is_terminal

CLOSEOUT_TAG = "lane:closeout"
CLOSEOUT_PARENT_IDLE_MAX_DAYS = 7
CLOSEOUT_LANE_STALE_MAX_DAYS = 7


def _iso_older_than_days(ts: Any, max_days: int) -> bool:
    if max_days <= 0:
        return False
    if ts is None or ts == "":
        return False
    try:
        normalized = str(ts).replace("Z", "+00:00")
        parsed_dt = datetime.fromisoformat(normalized)
        if parsed_dt.tzinfo is None:
            parsed_dt = parsed_dt.replace(tzinfo=UTC)
        delta = datetime.now(UTC) - parsed_dt.astimezone(UTC)
        return delta.total_seconds() > max_days * 86400
    except (TypeError, ValueError):
        return False


def lane_status_query_pointer(root_id: str) -> str:
    return f"liaison-tick.py --root {root_id} --lane-status"


def _already_emitted(state: dict[str, Any], key: str) -> bool:
    return bool((state.get("lane_closeouts_emitted") or {}).get(key))


def _projected_worker_turn(state: dict[str, Any], lane_id: str) -> int:
    raw = (state.get("lane_closeout_worker_turn") or {}).get(lane_id)
    try:
        return int(raw) if raw is not None else 0
    except (TypeError, ValueError):
        return 0


def _mark_projected_worker_turn(state: dict[str, Any], lane_id: str, worker_turn: int) -> None:
    projected = dict(state.get("lane_closeout_worker_turn") or {})
    projected[lane_id] = worker_turn
    state["lane_closeout_worker_turn"] = projected


def _mark_emitted(state: dict[str, Any], key: str) -> None:
    emitted = dict(state.get("lane_closeouts_emitted") or {})
    emitted[key] = _utcnow_iso()
    state["lane_closeouts_emitted"] = emitted


def _bus_closeout_matches_record(prior: dict[str, Any], record: dict[str, Any]) -> bool:
    """True when parent-root debrief already reflects this worker settlement."""
    if bool(prior.get("abandoned")) != bool(record.get("abandoned")):
        return False
    return (
        str(prior.get("settled") or "") == str(record.get("settled") or "")
        and str(prior.get("landed") or "") == str(record.get("landed") or "")
    )


def _pytest_blocks_live_bus(client: httpx.Client) -> bool:
    return bool(
        os.environ.get("PYTEST_CURRENT_TEST")
        and os.environ.get("BUS_WATCH_ALLOW_LIVE_BUS_IN_TESTS") != "1"
        and isinstance(client, httpx.Client)
        and not isinstance(getattr(client, "_transport", None), httpx.MockTransport)
    )


def _send_closeout(
    client: httpx.Client,
    *,
    parent_root: str,
    subject: str,
    body: str,
    lane: str,
    terminal_status: str,
    abandoned: bool,
) -> dict[str, Any] | None:
    if _pytest_blocks_live_bus(client):
        return {"ok": False, "error": "pytest_live_bus_blocked"}
    try:
        resp = client.post(
            "/threads/send",
            json={
                "thread": parent_root,
                "to": "web-anthropic",
                "from": "cursor",
                "subject": subject,
                "body": body,
                "tags": [CLOSEOUT_TAG],
            },
        )
    except httpx.HTTPError as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"[:200]}
    if resp.status_code >= 400:
        return {"ok": False, "error": f"http_{resp.status_code}"}
    try:
        data = resp.json()
    except ValueError:
        return {"ok": False, "error": "non_json_response"}
    turn = data.get("turn") if isinstance(data, dict) else None
    turn_number = turn.get("turn_number") if isinstance(turn, dict) else None
    emit_lane_closeout_observed(
        root=parent_root,
        lane=lane,
        terminal_status=terminal_status,
        abandoned=abandoned,
        turn=int(turn_number) if turn_number is not None else None,
    )
    return {"ok": True, "turn_number": turn_number}


def post_lane_closeout(
    client: httpx.Client,
    *,
    parent_root: str,
    record: dict[str, Any],
    abandoned: bool = False,
) -> dict[str, Any] | None:
    """Post one structured closeout turn on ``parent_root``."""
    return _send_closeout(
        client,
        parent_root=parent_root,
        subject=format_closeout_subject(record, abandoned=abandoned),
        body=format_closeout_body(record, abandoned=abandoned),
        lane=str(record.get("lane") or ""),
        terminal_status=str(record.get("terminal_status") or ""),
        abandoned=abandoned,
    )


def post_bulk_abandoned_closeout(
    client: httpx.Client,
    *,
    parent_root: str,
    records: list[dict[str, Any]],
) -> dict[str, Any] | None:
    """Post one digest turn for multiple abandoned lanes without worker closeouts."""
    if not records:
        return None
    lane_ids = [str(r.get("lane") or "") for r in records]
    lane_range = f"{lane_ids[0]}..{lane_ids[-1]}"
    count = len(lane_ids)
    digest_record: dict[str, Any] = {
        "abandoned": True,
        "bulk": True,
        "closed_at": _utcnow_iso(),
        "count": count,
        "lane": lane_range,
        "lane_range": lane_range,
        "lanes": lane_ids,
        "live": "unprobed",
        "parent_root": parent_root,
        "terminal_status": "abandoned",
    }
    return _send_closeout(
        client,
        parent_root=parent_root,
        subject=f"{ABANDONED_SUBJECT_PREFIX} agent-bus:{lane_range} ({count} abandoned)",
        body=format_closeout_body(digest_record, abandoned=True),
        lane=lane_range,
        terminal_status="abandoned",
        abandoned=True,
    )


def _stamp_idle_parent_lanes(
    parent_root: str,
    lanes: list[dict[str, Any]],
    state: dict[str, Any],
) -> None:
    for lane in lanes:
        if not isinstance(lane, dict) or not row_is_terminal(lane):
            continue
        lane_id = str(lane.get("id") or "")
        if lane_id and lane_id != parent_root:
            _mark_emitted(state, closeout_idempotency_key(lane_id, _terminal_status(lane)))


def observe_terminal_lane_closeouts(
    parent_root: str,
    lanes: list[dict[str, Any]],
    state: dict[str, Any],
    client: httpx.Client,
    *,
    fetch_turns: Callable[[str], Any],
    parent_thread: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Emit idempotent closeout/abandon rows when digest lanes go terminal."""
    if parent_thread is not None:
        ps = str(parent_thread.get("status") or "").lower()
        if ps == "closed" or _iso_older_than_days(
            parent_thread.get("updated_at"), CLOSEOUT_PARENT_IDLE_MAX_DAYS
        ):
            _stamp_idle_parent_lanes(parent_root, lanes, state)
            return []

    posted: list[dict[str, Any]] = []
    abandoned_pending: list[tuple[dict[str, Any], str]] = []
    existing_by_lane = {
        str(row.get("lane") or ""): row
        for row in query_lane_closeouts(client, parent_root, last=200)
    }

    for lane in lanes:
        if not isinstance(lane, dict) or not row_is_terminal(lane):
            continue
        lane_id = str(lane.get("id") or "")
        if not lane_id or lane_id == parent_root:
            continue
        terminal_status = _terminal_status(lane)
        key = closeout_idempotency_key(lane_id, terminal_status)
        if _iso_older_than_days(lane.get("updated_at"), CLOSEOUT_LANE_STALE_MAX_DAYS):
            _mark_emitted(state, key)
            continue
        abandoned = terminal_status == "abandoned"
        turns = fetch_turns(lane_id)
        turn_list = turns if isinstance(turns, list) else []
        closeout_turn = _find_worker_closeout_turn([t for t in turn_list if isinstance(t, dict)])
        worker_turn = int(closeout_turn.get("turn_number") or 0) if closeout_turn else 0
        if worker_turn > 0 and _projected_worker_turn(state, lane_id) >= worker_turn:
            continue
        if worker_turn <= 0 and _already_emitted(state, key):
            continue
        worker_text = str(closeout_turn.get("body") or "") if closeout_turn else ""
        if abandoned and closeout_turn is None:
            abandoned_pending.append(
                (build_closeout_record(lane, parent_root=parent_root, abandoned=True), key)
            )
            continue
        record = build_closeout_record(
            lane, parent_root=parent_root, worker_closeout_text=worker_text, abandoned=abandoned
        )
        if worker_text and closeout_needs_friction_file(worker_text):
            fid, ferr = file_closeout_friction(
                worker_text=worker_text, lane_id=lane_id, worker_turn=worker_turn
            )
            if fid:
                record["friction_id"] = fid
            if ferr:
                record["friction_error"] = ferr
        prior = existing_by_lane.get(lane_id)
        if prior is not None and _bus_closeout_matches_record(prior, record):
            _mark_emitted(state, key)
            if worker_turn > 0:
                _mark_projected_worker_turn(state, lane_id, worker_turn)
            continue
        result = post_lane_closeout(client, parent_root=parent_root, record=record, abandoned=abandoned)
        if result and result.get("ok"):
            _mark_emitted(state, key)
            if worker_turn > 0:
                _mark_projected_worker_turn(state, lane_id, worker_turn)
            existing_by_lane[lane_id] = record
            posted.append(record)

    if len(abandoned_pending) == 1:
        record, key = abandoned_pending[0]
        lane_id = str(record.get("lane") or "")
        prior = existing_by_lane.get(lane_id)
        if prior is not None and _bus_closeout_matches_record(prior, record):
            _mark_emitted(state, key)
        elif (result := post_lane_closeout(client, parent_root=parent_root, record=record, abandoned=True)) and result.get("ok"):
            _mark_emitted(state, key)
            existing_by_lane[lane_id] = record
            posted.append(record)
    elif len(abandoned_pending) > 1:
        records = [r for r, _ in abandoned_pending]
        if (result := post_bulk_abandoned_closeout(client, parent_root=parent_root, records=records)) and result.get("ok"):
            for _, k in abandoned_pending:
                _mark_emitted(state, k)
            ids = [str(r.get("lane") or "") for r in records]
            posted.append(
                {
                    "abandoned": True,
                    "bulk": True,
                    "count": len(ids),
                    "lane": f"{ids[0]}..{ids[-1]}",
                    "lane_range": f"{ids[0]}..{ids[-1]}",
                    "lanes": ids,
                    "terminal_status": "abandoned",
                }
            )
    return posted


def maybe_emit_abandoned_on_grace_expiry(
    grace: Any,
    producer: dict[str, Any],
    turn_count: int,
    *,
    parent_root: str,
    lane: dict[str, Any],
    state: dict[str, Any],
    client: httpx.Client,
) -> dict[str, Any] | None:
    """Post ``LANE ABANDONED`` when producer grace expires without closeout (AC5)."""
    if not grace.observe(producer, turn_count):
        return None
    key = closeout_idempotency_key(str(lane.get("id") or ""), "abandoned")
    if _already_emitted(state, key):
        return None
    record = build_closeout_record({**lane, "lifecycle": "abandoned"}, parent_root=parent_root, abandoned=True)
    result = post_lane_closeout(client, parent_root=parent_root, record=record, abandoned=True)
    if result and result.get("ok"):
        _mark_emitted(state, key)
    return result


__all__ = [
    "ABANDONED_SUBJECT_PREFIX",
    "CLOSEOUT_SUBJECT_PREFIX",
    "CLOSEOUT_TAG",
    "build_closeout_record",
    "closeout_idempotency_key",
    "closeout_text_has_assertion_id",
    "format_closeout_body",
    "format_closeout_subject",
    "friction_note_from_closeout",
    "friction_owner_from_closeout",
    "lane_status_query_pointer",
    "maybe_emit_abandoned_on_grace_expiry",
    "observe_terminal_lane_closeouts",
    "parse_closeout_turn",
    "post_bulk_abandoned_closeout",
    "post_lane_closeout",
    "probe_live",
    "query_lane_closeouts",
]
