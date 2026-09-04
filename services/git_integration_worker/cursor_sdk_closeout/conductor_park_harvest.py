"""GIW park-harvest reactor leg (todo:premature-stop-awareness-substrate Phase B).

Fires at terminal only — classifies parked-with-harvest-owed and posts harvest
arm-recipe on the summoning thread. Does **not** observe later reply arrival;
that wake belongs to the armed watcher (Phase A).
"""

from __future__ import annotations

import json
import os
import time
from typing import Any

import httpx
from bus_watch.park_harvest import harvest_still_owed, mission_open
from claude_bundles.conductor_stop import EXIT_PERSIST_STOPS
from transport_utils import DEFAULT_AGENT_BUS_URL, make_sync_client
from universal_logging import get_logger

from services.git_integration_worker.cursor_dispatch_ledger import (
    CursorDispatchLedger,
)
from services.git_integration_worker.cursor_sdk_hop_events import (
    emit_frontier_sdk_conductor_hop_park_harvest,
)
from services.git_integration_worker.cursor_sdk_ledger_hop import (
    hop_fields_from_record_json,
)

logger = get_logger(__name__)

_HOP_PARK_HARVEST_FIRED_KEY = "hop_park_harvest_fired_at"
_HOP_PARK_HARVEST_CONTINUED_KEY = "hop_park_harvest_continued_at"


def _closeout_tokens_from_row(row: dict[str, Any]) -> frozenset[str]:
    record_json = str(row.get("record_json") or "")
    try:
        data = json.loads(record_json) if record_json else {}
    except json.JSONDecodeError:
        data = {}
    if not isinstance(data, dict):
        return frozenset()
    raw = data.get("closeout_stop_tokens")
    if isinstance(raw, list):
        return frozenset(str(t).upper() for t in raw)
    return frozenset()


def _record_data(row: dict[str, Any]) -> dict[str, Any]:
    record_json = str(row.get("record_json") or "")
    try:
        data = json.loads(record_json) if record_json else {}
    except json.JSONDecodeError:
        data = {}
    return data if isinstance(data, dict) else {}


def _load_row(dispatch_id: str) -> dict[str, Any] | None:
    ledger = CursorDispatchLedger.instance()
    with ledger._connect() as conn:
        row = conn.execute(
            "SELECT * FROM cursor_sdk_dispatches WHERE dispatch_id=?",
            (dispatch_id,),
        ).fetchone()
    if row is None:
        return None
    return {k: row[k] for k in row.keys()}


def _is_conductor_row(row: dict[str, Any]) -> bool:
    from services.git_integration_worker.cursor_sdk_conductor_conflict import (
        _record_packet_kind,
    )

    return _record_packet_kind(str(row.get("record_json") or "")) == "conductor"


def _closeout_body_from_row(row: dict[str, Any]) -> str:
    rec = _record_data(row)
    body = rec.get("closeout_body")
    if isinstance(body, str) and body.strip():
        return body
    return str(row.get("closeout_body") or row.get("message") or "")


def _scoreboard_body_for_row(row: dict[str, Any]) -> str:
    rec = _record_data(row)
    uri = str(rec.get("scoreboard_uri") or rec.get("scoreboard") or "").strip()
    if uri.startswith("cortex://"):
        from pathlib import Path

        rel = uri[len("cortex://") :]
        path = Path("/mnt/torus/mcp-data/files") / rel
        if path.is_file():
            return path.read_text(encoding="utf-8")
    return ""


def park_harvest_owed(
    row: dict[str, Any],
    *,
    closeout_tokens: frozenset[str] | None = None,
    scoreboard_body: str | None = None,
) -> bool:
    """G1 R1 bind: terminal exit-persist + mission open + harvest owed + ¬hop_owed."""
    status = str(row.get("status") or "")
    if status not in ("completed", "failed", "cancelled"):
        return False
    tokens = closeout_tokens or _closeout_tokens_from_row(row)
    if not (tokens & EXIT_PERSIST_STOPS):
        return False
    rec = _record_data(row)
    if rec.get("hop_parked"):
        return False
    if rec.get(_HOP_PARK_HARVEST_FIRED_KEY):
        return False
    rec_harvest = rec.get("closeout_harvest_owed")
    if isinstance(rec_harvest, bool):
        if not rec_harvest:
            return False
    else:
        body = _closeout_body_from_row(row)
        if not harvest_still_owed(body=body):
            return False
    sb = scoreboard_body if scoreboard_body is not None else _scoreboard_body_for_row(row)
    if sb and not mission_open(scoreboard_body=sb):
        return False
    from services.git_integration_worker.cursor_sdk_closeout.conductor_hop import (
        hop_owed,
    )

    if hop_owed(row, closeout_tokens=tokens):
        return False
    return True


def build_park_harvest_arm_recipe(
    *,
    row: dict[str, Any],
    summoning_thread_id: str,
    closeout_turn: int | None = None,
) -> str:
    """Arm recipe for attended watcher (Phase A) on summoning thread."""
    work_key = str(row.get("work_key") or "mission")
    label = f"{work_key}-harvest"
    thread_id = summoning_thread_id
    rec = _record_data(row)
    turn_from_rec = rec.get("closeout_turn")
    turn_i = (
        closeout_turn
        if closeout_turn is not None
        else (int(turn_from_rec) if isinstance(turn_from_rec, int) else None)
    )
    after_turn = turn_i if turn_i is not None else 0
    scoreboard_uri = str(rec.get("scoreboard_uri") or rec.get("scoreboard") or "")
    lines = [
        "park-harvest: arm watcher for CDP reply arrival (Phase A).",
        f"scripts/watch-supervise.sh start --label {label} -- \\",
        "  scripts/watch-bus-consult-and-page.py \\",
        f"  --thread {thread_id} --after-turn {after_turn} \\",
        "  --from-agent web-anthropic --no-page \\",
    ]
    if scoreboard_uri:
        lines.append(f"  --scoreboard-uri {scoreboard_uri} \\")
    lines.append(f"  --label {label}")
    return "\n".join(lines)


def default_park_harvest_poster(thread_id: str, body: str) -> None:
    """POST harvest arm-recipe nudge on summoning thread — not team_dispatch."""
    token = os.environ.get("AGENT_BUS_TOKEN", "").strip()
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    payload = {
        "thread": thread_id,
        "from": "conductor-hop",
        "to": "cursor",
        "subject": f"park-harvest wake — {thread_id}",
        "body": body,
        "status": "open",
    }
    with make_sync_client(DEFAULT_AGENT_BUS_URL, timeout=15.0) as client:
        resp = client.post("/turns", json=payload, headers=headers)
    if resp.status_code >= 400:
        raise httpx.HTTPStatusError(
            f"park harvest post failed status={resp.status_code}",
            request=resp.request,
            response=resp,
        )


async def fire_park_harvest(
    row: dict[str, Any],
    *,
    poster: Any | None = None,
) -> bool:
    """Emit event + harvest nudge; stamp ledger idempotently on successful POST."""
    dispatch_id = str(row.get("dispatch_id") or "")
    thread_id = str(row.get("thread_id") or "")
    rec = _record_data(row)
    summoning_thread_id = str(rec.get("summoning_thread_id") or thread_id).strip()
    hop_fields = hop_fields_from_record_json(str(row.get("record_json") or ""))
    hop_seq = hop_fields.get("hop_seq")
    hop_seq_int = int(hop_seq) if isinstance(hop_seq, int) else 1
    closeout_turn = rec.get("closeout_turn")
    turn_i = int(closeout_turn) if isinstance(closeout_turn, int) else None

    body = build_park_harvest_arm_recipe(
        row=row,
        summoning_thread_id=summoning_thread_id,
        closeout_turn=turn_i,
    )
    try:
        (poster or default_park_harvest_poster)(summoning_thread_id, body)
    except Exception:  # noqa: BLE001
        logger.warning(
            "park harvest bus post failed dispatch=%s thread=%s",
            dispatch_id,
            summoning_thread_id,
            exc_info=True,
        )
        return False

    emit_frontier_sdk_conductor_hop_park_harvest(
        dispatch_id=dispatch_id,
        thread_id=thread_id,
        summoning_thread_id=summoning_thread_id,
        hop_seq=hop_seq_int,
    )

    ledger = CursorDispatchLedger.instance()
    ledger.merge_record_json(
        dispatch_id=dispatch_id,
        patch={_HOP_PARK_HARVEST_FIRED_KEY: time.time()},
    )
    return True


async def maybe_fire_conductor_park_harvest(*, dispatch_id: str) -> bool:
    """Post-terminal third leg: park_harvest_owed → event + harvest wake, never successor."""
    row = _load_row(dispatch_id)
    if row is None or not _is_conductor_row(row):
        return False
    closeout_tokens = _closeout_tokens_from_row(row)
    if not park_harvest_owed(row, closeout_tokens=closeout_tokens):
        return False
    return await fire_park_harvest(row)


def reply_arrived_on_thread(
    *,
    thread_id: str,
    after_turn: int,
    from_agent: str = "web-anthropic",
    snapshot_fn: Any | None = None,
) -> bool:
    """Bus snapshot: ``first_reply_from`` after ``after_turn`` (fail-closed)."""
    if snapshot_fn is not None:
        return bool(snapshot_fn(thread_id, after_turn, from_agent))

    params = {
        "after_turn": after_turn,
        "wait": 0,
        "completion": "first_reply_from",
        "from_agent": from_agent,
    }
    token = os.environ.get("AGENT_BUS_TOKEN", "").strip()
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    try:
        with make_sync_client(DEFAULT_AGENT_BUS_URL, timeout=15.0) as client:
            resp = client.get(
                f"/threads/{thread_id}/wait",
                params=params,
                headers=headers,
            )
        if resp.status_code in (404, 422):
            return False
        if resp.status_code >= 400:
            return False
        payload = resp.json()
        if not isinstance(payload, dict):
            return False
        return bool(payload.get("complete"))
    except Exception:  # noqa: BLE001
        logger.warning(
            "park harvest continue reply snapshot failed thread=%s after_turn=%s",
            thread_id,
            after_turn,
            exc_info=True,
        )
        return False


def park_harvest_continue_owed(
    row: dict[str, Any],
    *,
    closeout_tokens: frozenset[str] | None = None,
    reply_fn: Any | None = None,
) -> bool:
    """R-B2 bind §6: PARKED_TRANSPORT terminal + Phase B fired + reply arrived."""
    status = str(row.get("status") or "")
    if status not in ("completed", "failed", "cancelled"):
        return False
    tokens = closeout_tokens or _closeout_tokens_from_row(row)
    if "PARKED_TRANSPORT" not in tokens:
        return False
    rec = _record_data(row)
    if not rec.get(_HOP_PARK_HARVEST_FIRED_KEY):
        return False
    if rec.get(_HOP_PARK_HARVEST_CONTINUED_KEY):
        return False
    if rec.get("hop_parked"):
        return False
    from services.git_integration_worker.cursor_sdk_park import _successor_admitted

    dispatch_id = str(row.get("dispatch_id") or "")
    record_json = str(row.get("record_json") or "")
    ledger = CursorDispatchLedger.instance()
    with ledger._connect() as conn:
        if _successor_admitted(
            conn, predecessor_id=dispatch_id, record_json=record_json
        ):
            return False
    thread_id = str(row.get("thread_id") or "")
    closeout_turn = rec.get("closeout_turn")
    if not thread_id or not isinstance(closeout_turn, int):
        return False
    snapshot = reply_fn or (
        lambda tid, turn, agent: reply_arrived_on_thread(
            thread_id=tid, after_turn=turn, from_agent=agent
        )
    )
    if not snapshot(thread_id, closeout_turn, "web-anthropic"):
        return False
    from services.git_integration_worker.cursor_sdk_closeout.conductor_hop import (
        conductor_has_live_nested,
        live_conductor_row_on_thread,
        mission_open_for_row,
    )
    from services.git_integration_worker.cursor_sdk_closeout.conductor_hop_budget import (
        evaluate_hop_budget,
    )

    if live_conductor_row_on_thread(
        thread_id=thread_id, exclude_dispatch_id=dispatch_id
    ):
        return False
    if conductor_has_live_nested(dispatch_id=dispatch_id):
        return False
    if not mission_open_for_row(row, closeout_tokens=tokens):
        return False
    verdict = evaluate_hop_budget(row, closeout_tokens=tokens)
    if not verdict.ok or verdict.park:
        return False
    return True


async def fire_park_harvest_continue(row: dict[str, Any]) -> bool:
    """Admit park-harvest successor via hop path (bind §6 X-fresh)."""
    from services.git_integration_worker.cursor_sdk_closeout.conductor_hop import (
        build_hop_team_dispatch_body,
        post_conductor_hop_team_dispatch,
    )
    from services.git_integration_worker.cursor_sdk_hop_events import (
        emit_frontier_sdk_conductor_hop_admit_failed,
        emit_frontier_sdk_conductor_hop_admitted,
    )
    from services.git_integration_worker.cursor_sdk_ledger_hop import merge_hop_patch

    dispatch_id = str(row.get("dispatch_id") or "")
    thread_id = str(row.get("thread_id") or "")
    body = build_hop_team_dispatch_body(row, hop_reason_override="park_harvest")
    if body is None:
        return False
    hop_seq = int(body.get("hop_seq") or 1)
    ok, detail = await post_conductor_hop_team_dispatch(body)
    record_json = str(row.get("record_json") or "")
    if ok:
        successor = (
            str(detail.get("dispatch_id") or "")
            or str(detail.get("execution_id") or "")
        )
        if not successor:
            logger.warning(
                "park harvest continue admit ok but no successor id "
                "dispatch_id=%s detail=%s",
                dispatch_id,
                detail,
            )
            return False
        merged = merge_hop_patch(record_json, {"hop_successor": successor})
        try:
            data = json.loads(merged) if merged else {}
        except json.JSONDecodeError:
            data = {}
        if isinstance(data, dict):
            data[_HOP_PARK_HARVEST_CONTINUED_KEY] = time.time()
            merged = json.dumps(data, sort_keys=True, separators=(",", ":"))
        ledger = CursorDispatchLedger.instance()
        with ledger._connect() as conn:
            conn.execute(
                "UPDATE cursor_sdk_dispatches SET record_json=? WHERE dispatch_id=?",
                (merged, dispatch_id),
            )
        emit_frontier_sdk_conductor_hop_admitted(
            predecessor_dispatch_id=dispatch_id,
            successor_dispatch_id=successor,
            thread_id=thread_id,
            hop_seq=hop_seq,
            hop_reason="park_harvest",
        )
        return True
    error_text = json.dumps(detail, sort_keys=True)[:500]
    merged = merge_hop_patch(
        record_json,
        {
            "hop_admit_error": {
                "error": error_text,
                "status_code": detail.get("status_code"),
            }
        },
    )
    ledger = CursorDispatchLedger.instance()
    with ledger._connect() as conn:
        conn.execute(
            "UPDATE cursor_sdk_dispatches SET record_json=? WHERE dispatch_id=?",
            (merged, dispatch_id),
        )
    emit_frontier_sdk_conductor_hop_admit_failed(
        dispatch_id=dispatch_id,
        thread_id=thread_id,
        hop_seq=hop_seq,
        hop_reason="park_harvest",
        error=error_text,
        status_code=detail.get("status_code"),
    )
    return False


__all__ = [
    "build_park_harvest_arm_recipe",
    "fire_park_harvest",
    "fire_park_harvest_continue",
    "maybe_fire_conductor_park_harvest",
    "park_harvest_continue_owed",
    "park_harvest_owed",
    "reply_arrived_on_thread",
]
