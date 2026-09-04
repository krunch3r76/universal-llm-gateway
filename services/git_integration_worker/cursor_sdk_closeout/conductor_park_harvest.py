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
    body = _closeout_body_from_row(row)
    sb = scoreboard_body if scoreboard_body is not None else _scoreboard_body_for_row(row)
    if sb and not mission_open(scoreboard_body=sb):
        return False
    if not harvest_still_owed(body=body):
        return False
    from services.git_integration_worker.cursor_sdk_closeout.conductor_hop import hop_owed

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
    after_turn = closeout_turn if closeout_turn is not None else 0
    rec = _record_data(row)
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
) -> None:
    """Emit event + harvest nudge; stamp ledger idempotently."""
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


async def maybe_fire_conductor_park_harvest(*, dispatch_id: str) -> bool:
    """Post-terminal third leg: park_harvest_owed → event + harvest wake, never successor."""
    row = _load_row(dispatch_id)
    if row is None or not _is_conductor_row(row):
        return False
    closeout_tokens = _closeout_tokens_from_row(row)
    if not park_harvest_owed(row, closeout_tokens=closeout_tokens):
        return False
    await fire_park_harvest(row)
    return True


__all__ = [
    "build_park_harvest_arm_recipe",
    "fire_park_harvest",
    "maybe_fire_conductor_park_harvest",
    "park_harvest_owed",
]
