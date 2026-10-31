"""GIW park-harvest reactor leg (todo:premature-stop-awareness-substrate Phase B).

Fires at terminal only — classifies parked-with-harvest-owed and posts harvest
arm-recipe on the summoning thread. Does **not** observe later reply arrival;
that wake belongs to the armed watcher (Phase A).
"""

from __future__ import annotations

import json
import os
import re
import time
from datetime import datetime
from typing import Any

import httpx
from bus_watch.park_harvest import mission_open
from claude_bundles.conductor_stop import EXIT_PERSIST_STOPS
from transport_utils import DEFAULT_AGENT_BUS_URL, make_sync_client
from universal_logging import get_logger

from services.git_integration_worker.cursor_dispatch_ledger import (
    CursorDispatchLedger,
)
from services.git_integration_worker.cursor_sdk_hop_events import (
    emit_frontier_sdk_conductor_hop_park_harvest,
    emit_frontier_sdk_conductor_hop_park_harvest_continue_refused,
)
from services.git_integration_worker.cursor_sdk_ledger_hop import (
    hop_fields_from_record_json,
)

logger = get_logger(__name__)

_HOP_PARK_HARVEST_FIRED_KEY = "hop_park_harvest_fired_at"
_HOP_PARK_HARVEST_CONTINUED_KEY = "hop_park_harvest_continued_at"
_HOP_PARK_HARVEST_REFUSED_PREDICATE_KEY = "hop_park_harvest_refused_predicate"
_CONSULT_PENDING_CONTINUED_KEY = "hop_consult_harvest_continued_at"
_NO_PROGRESS_PRODUCER_CONTINUED_KEY = "hop_no_progress_producer_continued_at"
_PARK_REASON_NO_PROGRESS_CAP = "hop_budget_no_progress_cap"
HOP_REASON_PRODUCER_HARVEST = "producer_harvest"


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


def _closeout_declares_done(row: dict[str, Any], tokens: frozenset[str]) -> bool:
    """True when the stamp or the closeout body carries stop: DONE.

    Continue callers sometimes pass a stamp that names only their own token.
    A body that also says DONE is still a finished mission, and admitting a
    successor from it would treat a terminal closeout as a consult or a
    park-harvest.
    """
    if "DONE" in tokens:
        return True
    body = _closeout_body_from_row(row)
    if not body:
        return False
    from claude_bundles.conductor_stop import parse_designed_stop_tokens

    return "DONE" in parse_designed_stop_tokens(body).tokens


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
    from services.git_integration_worker.cursor_sdk_conductor_identity import (
        is_conductor_dispatch_row,
    )

    return is_conductor_dispatch_row(row)


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


def harvest_owed_at_terminal(row: dict[str, Any]) -> bool:
    """Whether harvest is still owed on a terminal row (body, bool stamp, or empty)."""
    from services.git_integration_worker.cursor_sdk_closeout.conductor_exit_reasons import (
        closeout_harvest_owed_from_row,
    )

    return closeout_harvest_owed_from_row(row)


def _body_grades_park_harvest_owed(row: dict[str, Any]) -> bool:
    """True when closeout prose grades ``conductor_park_harvest_owed``.

    Hop authority stores only unfenced ``stop:`` lines. A ``PARKED_TRANSPORT``
    mention the grader still sees (fenced footer, table cell) leaves
    ``closeout_stop_tokens`` without an exit-persist token, ``hop_owed`` stays
    true, and the reactor emits ``next_admit_blocked`` without the wake.
    ``ROW_HOP`` in the same prose wins, matching the degraded-reason order.
    """
    body = _closeout_body_from_row(row)
    if not body.strip():
        return False
    from claude_bundles.conductor_stop import parse_stop_tokens

    prose_tokens = parse_stop_tokens(body).tokens
    if "ROW_HOP" in prose_tokens:
        return False
    from services.git_integration_worker.cursor_sdk_closeout.conductor_exit_reasons import (
        _parked_transport_harvest_is_live,
    )

    return _parked_transport_harvest_is_live(body, prose_tokens)


def park_harvest_owed(
    row: dict[str, Any],
    *,
    closeout_tokens: frozenset[str] | None = None,
    scoreboard_body: str | None = None,
) -> bool:
    """G1 R1 bind: terminal exit-persist + mission open + harvest owed + ¬hop_owed.

    A body that grades ``conductor_park_harvest_owed`` owes the same wake when
    the stored token list omitted ``PARKED_TRANSPORT``.
    """
    status = str(row.get("status") or "")
    if status not in ("completed", "failed", "cancelled"):
        return False
    tokens = closeout_tokens or _closeout_tokens_from_row(row)
    has_exit_persist = bool(tokens & EXIT_PERSIST_STOPS)
    body_park = _body_grades_park_harvest_owed(row) if not has_exit_persist else False
    if not has_exit_persist and not body_park:
        return False
    rec = _record_data(row)
    if rec.get("hop_parked"):
        return False
    if rec.get("park"):
        # GIW park_for_restart: the wake is the substrate's own resume child
        # (cursor_sdk_park_resume), not a CDP reply — no harvest arm recipe.
        return False
    if rec.get(_HOP_PARK_HARVEST_FIRED_KEY):
        return False
    if not harvest_owed_at_terminal(row) and not body_park:
        return False
    sb = (
        scoreboard_body
        if scoreboard_body is not None
        else _scoreboard_body_for_row(row)
    )
    if sb and not mission_open(scoreboard_body=sb):
        return False
    from services.git_integration_worker.cursor_sdk_closeout.conductor_hop import (
        _nested_harvest_head,
        hop_owed,
    )

    if _nested_harvest_head(row, rec) is not None:
        return False
    if not body_park:
        if hop_owed(row, closeout_tokens=tokens):
            return False
    return True


def _summoning_thread_uses_xvfb_push_wake(thread_id: str) -> bool:
    """True when the summoning house is subscribed on Cursor display :8."""
    try:
        from agent_bus_store.db.endpoint_subscriptions import resolve_endpoint

        resolved = resolve_endpoint("cursor", str(thread_id).strip())
    except Exception:  # noqa: BLE001 — best-effort classification
        return False
    if resolved is None:
        return False
    _route, row = resolved
    return (
        str(row.get("surface") or "") == "cursor"
        and str(row.get("display") or "") in {"8", ":8"}
        and str(row.get("state") or "") == "subscribed"
    )


def build_park_harvest_arm_recipe(
    *,
    row: dict[str, Any],
    summoning_thread_id: str,
    closeout_turn: int | None = None,
    push_wake_only: bool = False,
) -> str:
    """Arm recipe for attended watcher (Phase A) on summoning thread."""
    if push_wake_only:
        return (
            "park-harvest: :8 push-wake house — do not arm watch-supervise.sh or "
            "watch-bus-consult-and-page.py. Wait for endpoint_wake_dispatcher -> "
            "display-chat-wake (BRIDGE_WAKE) on the subscribed tab; harvest on wake."
        )
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
    # Without one of these the poller is producer-less and cannot stall-pop, so a
    # dead CDP leg reads as "still waiting" forever (a:33486).
    producer_exec = str(rec.get("cdp_execution_id") or rec.get("execution_id") or "")
    producer_arg = (
        f"--execution-id {producer_exec}" if producer_exec else "--no-producer"
    )
    lines = [
        "park-harvest: arm watcher for CDP reply arrival (Phase A).",
        "Leg 1 — detached poller:",
        f"scripts/watch-supervise.sh start --label {label} -- \\",
        "  scripts/watch-bus-consult-and-page.py \\",
        f"  --thread {thread_id} --after-turn {after_turn} \\",
        "  --from-agent web-anthropic --no-page \\",
        f"  {producer_arg} \\",
    ]
    if scoreboard_uri:
        lines.append(f"  --scoreboard-uri {scoreboard_uri} \\")
    lines.append(f"  --label {label}")
    lines.extend(
        [
            "Leg 2 — same turn (before close): background tail, no notify_on_output. Shell completion is the one harvest turn (friction 37400).",
            f"scripts/watch-supervise.sh tail --label {label}  # block_until_ms: 0; exits when state.json status=complete",
            "Leg 3 — on wake: get qualifying turn + relay in chat.",
        ]
    )
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
    """Emit park-harvest event and stamp ledger; bus wake only when ``poster`` is set."""
    dispatch_id = str(row.get("dispatch_id") or "")
    thread_id = str(row.get("thread_id") or "")
    rec = _record_data(row)
    summoning_thread_id = str(rec.get("summoning_thread_id") or "").strip()
    if not summoning_thread_id:
        logger.warning(
            "park harvest skipped — summoning_thread_id unset dispatch=%s worker=%s",
            dispatch_id,
            thread_id,
        )
        return False
    hop_fields = hop_fields_from_record_json(str(row.get("record_json") or ""))
    hop_seq = hop_fields.get("hop_seq")
    hop_seq_int = int(hop_seq) if isinstance(hop_seq, int) else 1
    closeout_turn = rec.get("closeout_turn")
    turn_i = int(closeout_turn) if isinstance(closeout_turn, int) else None

    push_wake_only = _summoning_thread_uses_xvfb_push_wake(summoning_thread_id)
    body = build_park_harvest_arm_recipe(
        row=row,
        summoning_thread_id=summoning_thread_id,
        closeout_turn=turn_i,
        push_wake_only=push_wake_only,
    )
    if poster is not None:
        try:
            poster(summoning_thread_id, body)
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


def _fetch_thread_turns(thread_id: str) -> list[dict[str, Any]] | None:
    """Sync GET /turns?thread=<id>; None on transport/parse failure."""
    token = os.environ.get("AGENT_BUS_TOKEN", "").strip()
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    try:
        with make_sync_client(DEFAULT_AGENT_BUS_URL, timeout=2.0) as client:
            resp = client.get(
                "/turns",
                params={"thread": thread_id},
                headers=headers,
            )
        if resp.status_code >= 400:
            return None
        payload = resp.json()
        if not isinstance(payload, dict):
            return None
        turns = payload.get("turns")
        if not isinstance(turns, list):
            return None
        return [t for t in turns if isinstance(t, dict)]
    except Exception:  # noqa: BLE001
        return None


def _watermark_at_instant_from_turns(
    turns: list[dict[str, Any]],
    closeout_instant: str,
) -> int | None:
    """Latest turn at or before ``closeout_instant``; 0 when empty at that instant."""
    if not turns:
        return 0
    try:
        anchor = datetime.fromisoformat(closeout_instant.replace("Z", "+00:00"))
    except ValueError:
        return None
    best = 0
    for turn in turns:
        turn_number = turn.get("turn_number")
        created_at = turn.get("created_at")
        if not isinstance(turn_number, int) or not created_at:
            continue
        try:
            ts = datetime.fromisoformat(str(created_at).replace("Z", "+00:00"))
        except ValueError:
            continue
        if ts <= anchor and turn_number > best:
            best = turn_number
    return best


def resolve_consult_summoning_watermark_at_instant(
    *,
    thread_id: str,
    closeout_instant: str,
) -> int | None:
    """Latest turn at or before ``closeout_instant``; 0 when empty at that instant."""
    turns = _fetch_thread_turns(thread_id)
    if turns is None:
        return None
    return _watermark_at_instant_from_turns(turns, closeout_instant)


def _maybe_retry_summoning_watermark(
    *,
    dispatch_id: str,
    rec: dict[str, Any],
) -> dict[str, Any] | None:
    """One-shot closeout-anchored watermark stamp via ``merge_record_json``."""
    summoning_id = str(rec.get("summoning_thread_id") or "").strip()
    if not summoning_id:
        return None
    if isinstance(rec.get("consult_summoning_after_turn"), int):
        return rec
    stamp_err = rec.get("consult_summoning_stamp_error")
    if not isinstance(stamp_err, dict):
        return rec
    closeout_instant = stamp_err.get("closeout_instant")
    if not isinstance(closeout_instant, str) or not closeout_instant.strip():
        return rec
    watermark = resolve_consult_summoning_watermark_at_instant(
        thread_id=summoning_id,
        closeout_instant=closeout_instant,
    )
    if watermark is None:
        return rec
    patch = {"consult_summoning_after_turn": watermark}
    ledger = CursorDispatchLedger.instance()
    ledger.merge_record_json(dispatch_id=dispatch_id, patch=patch)
    with ledger._connect() as conn:
        row = conn.execute(
            "SELECT record_json FROM cursor_sdk_dispatches WHERE dispatch_id=?",
            (dispatch_id,),
        ).fetchone()
    if row is None:
        return rec
    try:
        merged = json.loads(row["record_json"] or "{}")
    except json.JSONDecodeError:
        return rec
    return merged if isinstance(merged, dict) else rec


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


_CDP_REPLY_SUBJECT_RE = re.compile(
    r"(?i)^cdp reply — `(?P<id>[^`\n]+)`$"
)
_CDP_REPLY_SUBJECT_PLAIN_RE = re.compile(
    r"(?i)^cdp reply — (?P<id>\S+)\s*$"
)
_EXECUTION_ID_LINE_RE = re.compile(
    r"(?i)^execution_id:\s*`?(?P<id>[^`\n]+)`?\s*$"
)
# Park-harvest continue only: inline NEXT_ADMIT when line-anchored parse misses (#124).
_MIDLINE_NEXT_ADMIT_HARVEST_RE = re.compile(
    r"(?i)NEXT_ADMIT\s*:\s*harvest\s+"
    r"(?:`?"
    r"(?P<id>[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}"
    r"|[0-9a-f]{12}-[0-9a-f]{8}"
    r"|[0-9a-f]{8,32})"
    r"`?(?![0-9a-z_-]))"
)
_CDP_TERMINAL_FAILURE_SUBJECT_RE = re.compile(
    r"(?i)^cdp (?:FAILED|UNVERIFIED) — `(?P<id>[^`\n]+)`$"
)
_CDP_TERMINAL_FAILURE_SUBJECT_PLAIN_RE = re.compile(
    r"(?i)^cdp (?:FAILED|UNVERIFIED) — (?P<id>\S+)\s*$"
)


def _park_harvest_target_token(guard: str) -> str | None:
    """Harvest id for park-harvest continue; line-anchored NEXT_ADMIT wins over inline."""
    from services.git_integration_worker.cursor_sdk_closeout.conductor_hop import (
        _harvest_target_token,
        last_next_admit_payload,
    )

    scoped = re.sub(r"```[\s\S]*?```", "", guard or "")
    token = _harvest_target_token(scoped)
    if token is not None:
        return token
    if last_next_admit_payload(scoped) is not None:
        return None
    distinct: set[str] = set()
    chosen: str | None = None
    for match in _MIDLINE_NEXT_ADMIT_HARVEST_RE.finditer(scoped):
        distinct.add(str(match.group("id")).lower())
        chosen = str(match.group("id"))
    if len(distinct) != 1:
        return None
    return chosen


def _harvest_id_boundary_match(candidate: str, token: str) -> bool:
    if not candidate or not token:
        return False
    lower_c = candidate.lower()
    lower_t = token.lower()
    if lower_c == lower_t:
        return True

    def _prefix_at_boundary(longer: str, shorter: str) -> bool:
        if not longer.startswith(shorter):
            return False
        if len(longer) == len(shorter):
            return True
        return longer[len(shorter)] not in "0123456789abcdef"

    if len(lower_c) >= len(lower_t):
        return _prefix_at_boundary(lower_c, lower_t)
    return _prefix_at_boundary(lower_t, lower_c)


def _turn_cdp_reply_candidate_id(turn: dict[str, Any]) -> str | None:
    subject = str(turn.get("subject") or "").strip()
    for pattern in (_CDP_REPLY_SUBJECT_RE, _CDP_REPLY_SUBJECT_PLAIN_RE):
        match = pattern.match(subject)
        if match:
            return str(match.group("id")).strip()
    if subject:
        return None
    body = str(turn.get("body") or "")
    from agent_bus_store.body_auto_spill import AUTO_OVERFLOW_BRIEFING

    if AUTO_OVERFLOW_BRIEFING in body:
        return None
    for line in body.splitlines():
        line_match = _EXECUTION_ID_LINE_RE.match(line.strip())
        if line_match:
            return str(line_match.group("id")).strip()
    return None


def _turn_cdp_terminal_failure_candidate_id(turn: dict[str, Any]) -> str | None:
    """Short execution id from on-behalf ``cdp FAILED`` / ``cdp UNVERIFIED`` subjects."""
    subject = str(turn.get("subject") or "").strip()
    for pattern in (
        _CDP_TERMINAL_FAILURE_SUBJECT_RE,
        _CDP_TERMINAL_FAILURE_SUBJECT_PLAIN_RE,
    ):
        match = pattern.match(subject)
        if match:
            return str(match.group("id")).strip()
    return None


def _row_admitted_at_iso(row: dict[str, Any]) -> str | None:
    text = str(row.get("started_at") or "").strip()
    return text or None


def _turn_bus_author(turn: dict[str, Any]) -> str:
    """Agent-bus turn author from ``from`` or legacy ``from_agent``."""
    from agent_seat.registry import normalize_bus_address

    raw = turn.get("from")
    if raw is None or raw == "":
        raw = turn.get("from_agent")
    return normalize_bus_address(str(raw or ""))


def _harvest_reply_lower_bound_watermark_from_turns(
    turns: list[dict[str, Any]],
    *,
    started_iso: str | None,
    row_admit_iso: str | None,
) -> int | None:
    """Latest turn at or before max(CDP start, row admit); None stays fail-closed."""
    if started_iso is None and row_admit_iso is None:
        return None
    bounds: list[int] = []
    for instant in (started_iso, row_admit_iso):
        if instant is None:
            continue
        wm = _watermark_at_instant_from_turns(turns, instant)
        if wm is None:
            return None
        bounds.append(wm)
    if not bounds:
        return None
    return max(bounds)


def _evaluate_harvest_reply_execution(
    row: dict[str, Any], token: str
) -> tuple[bool, str | None]:
    """Shape B match in one bus pass — ``(True, None)`` or ``(False, predicate)``."""
    from services.git_integration_worker.cursor_sdk_closeout.conductor_hop import (
        _harvest_execution_started_at_iso,
    )

    rec = _record_data(row)
    closeout_turn = rec.get("closeout_turn")
    if not isinstance(closeout_turn, int):
        return False, "bounds_missing"
    thread_ids: list[str] = []
    worker_id = str(row.get("thread_id") or "").strip()
    summoning_id = str(rec.get("summoning_thread_id") or "").strip()
    for tid in (worker_id, summoning_id):
        if tid and tid not in thread_ids:
            thread_ids.append(tid)
    if not thread_ids:
        return False, "bounds_missing"
    started_iso = _harvest_execution_started_at_iso(token)
    row_admit_iso = _row_admitted_at_iso(row)
    if started_iso is None and row_admit_iso is None:
        return False, "bounds_missing"
    saw_started_at_floor = False
    saw_author_mismatch = False
    saw_wrong_id_after_watermark = False
    saw_web_after_watermark = False
    for tid in thread_ids:
        turns = _fetch_thread_turns(tid)
        if turns is None:
            return False, "fetch_turns_failed"
        watermark = _harvest_reply_lower_bound_watermark_from_turns(
            turns,
            started_iso=started_iso,
            row_admit_iso=row_admit_iso,
        )
        if watermark is None:
            return False, "bounds_missing"
        for turn in sorted(turns, key=lambda t: t.get("turn_number", 0)):
            turn_number = turn.get("turn_number")
            if not isinstance(turn_number, int):
                continue
            author = _turn_bus_author(turn)
            candidate = _turn_cdp_reply_candidate_id(turn)
            failure_id = _turn_cdp_terminal_failure_candidate_id(turn)
            if candidate is not None and _harvest_id_boundary_match(candidate, token):
                if turn_number <= watermark:
                    if author == "web-anthropic":
                        saw_started_at_floor = True
                    else:
                        saw_author_mismatch = True
                    continue
                if author != "web-anthropic":
                    saw_author_mismatch = True
                    continue
                return True, None
            if (
                turn_number > watermark
                and author == "web-anthropic"
                and failure_id is not None
                and _harvest_id_boundary_match(failure_id, token)
            ):
                return False, "reply_failed"
            if turn_number <= watermark:
                continue
            if author == "web-anthropic":
                saw_web_after_watermark = True
                if candidate is not None:
                    saw_wrong_id_after_watermark = True
    if saw_author_mismatch:
        return False, "author_mismatch"
    if saw_started_at_floor:
        return False, "started_at_floor"
    if saw_wrong_id_after_watermark:
        return False, "reply_id_mismatch"
    if saw_web_after_watermark:
        return False, "reply_id_mismatch"
    return False, "reply_absent"


def _reply_matches_harvest_execution(row: dict[str, Any], token: str) -> bool:
    """Shape B: CDP reply subject or ``execution_id:`` header matches harvest token."""
    matched, _ = _evaluate_harvest_reply_execution(row, token)
    return matched


def _hop_seq_from_row(row: dict[str, Any]) -> int:
    from .conductor_hop_seq import resolve_next_hop_seq_from_row

    rec = _record_data(row)
    seq = resolve_next_hop_seq_from_row(row, rec)
    if seq is None:
        return 0
    return seq


def _maybe_record_park_harvest_continue_refused(
    row: dict[str, Any],
    *,
    predicate: str,
    harvest_token: str | None,
) -> None:
    """Emit once per predicate change after park-harvest wake; stamp ledger."""
    dispatch_id = str(row.get("dispatch_id") or "")
    thread_id = str(row.get("thread_id") or "")
    if not dispatch_id or not thread_id:
        return
    rec = _record_data(row)
    if not rec.get(_HOP_PARK_HARVEST_FIRED_KEY):
        return
    prior = rec.get(_HOP_PARK_HARVEST_REFUSED_PREDICATE_KEY)
    if prior == predicate:
        return
    summoning_thread_id = str(rec.get("summoning_thread_id") or "").strip() or None
    CursorDispatchLedger.instance().merge_record_json(
        dispatch_id=dispatch_id,
        patch={_HOP_PARK_HARVEST_REFUSED_PREDICATE_KEY: predicate},
    )
    emit_frontier_sdk_conductor_hop_park_harvest_continue_refused(
        dispatch_id=dispatch_id,
        thread_id=thread_id,
        hop_seq=_hop_seq_from_row(row),
        predicate=predicate,
        harvest_token=harvest_token,
        summoning_thread_id=summoning_thread_id,
    )


def consult_pending_continue_owed(
    row: dict[str, Any],
    *,
    closeout_tokens: frozenset[str] | None = None,
    reply_fn: Any | None = None,
) -> bool:
    """D3: terminal + bare CONSULT_PENDING wait + web-anthropic reply + mission open."""
    from claude_bundles.conductor_stop import is_consult_pending_wait

    status = str(row.get("status") or "")
    if status not in ("completed", "failed", "cancelled"):
        return False
    tokens = closeout_tokens or _closeout_tokens_from_row(row)
    if _closeout_declares_done(row, tokens):
        return False
    rec = _record_data(row)
    if rec.get(_CONSULT_PENDING_CONTINUED_KEY):
        return False
    if rec.get("hop_parked") or rec.get("park"):
        return False
    body = _closeout_body_from_row(row)
    if not is_consult_pending_wait(body):
        return False
    from services.git_integration_worker.cursor_sdk_closeout.conductor_hop import (
        _nested_harvest_head,
    )

    head = _nested_harvest_head(row, rec)
    if head is not None and not head[1]:
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
    snapshot = reply_fn or (
        lambda tid, turn, agent: reply_arrived_on_thread(
            thread_id=tid, after_turn=turn, from_agent=agent
        )
    )
    worker_reply = False
    thread_id = str(row.get("thread_id") or "")
    closeout_turn = rec.get("closeout_turn")
    if thread_id and isinstance(closeout_turn, int):
        worker_reply = snapshot(thread_id, closeout_turn, "web-anthropic")

    summoning_id = str(rec.get("summoning_thread_id") or "").strip()
    summoning_reply = False
    if summoning_id:
        if not isinstance(rec.get("consult_summoning_after_turn"), int):
            rec = (
                _maybe_retry_summoning_watermark(dispatch_id=dispatch_id, rec=rec)
                or rec
            )
        watermark = rec.get("consult_summoning_after_turn")
        if isinstance(watermark, int):
            summoning_reply = snapshot(summoning_id, watermark, "web-anthropic")
    if not worker_reply and not summoning_reply:
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


def _park_harvest_stop_claim_exhausted(row: dict[str, Any]) -> bool:
    """True after two ``stop_not_claimed`` admits — no third POST on this path."""
    from services.git_integration_worker.cursor_sdk_ledger_hop import (
        park_harvest_stop_not_claimed_exhausted,
    )

    rec = _record_data(row)
    admit_err = rec.get("hop_admit_error")
    if not isinstance(admit_err, dict):
        return False
    return park_harvest_stop_not_claimed_exhausted(admit_err)


async def fire_consult_pending_continue(row: dict[str, Any]) -> bool:
    """Admit consult-harvest successor via hop path (D3)."""
    from services.git_integration_worker.cursor_sdk_closeout.conductor_hop import (
        _closeout_tokens_from_row,
        build_hop_team_dispatch_body,
        post_conductor_hop_team_dispatch,
    )
    from services.git_integration_worker.cursor_sdk_closeout.conductor_hop_budget import (
        evaluate_hop_budget,
    )
    from services.git_integration_worker.cursor_sdk_closeout.conductor_stop_service import (
        apply_hop_budget_announcer,
    )

    tokens = _closeout_tokens_from_row(row)
    verdict = evaluate_hop_budget(row, closeout_tokens=tokens)
    if not await apply_hop_budget_announcer(row, verdict):
        return False
    from services.git_integration_worker.cursor_sdk_hop_events import (
        emit_frontier_sdk_conductor_hop_admit_failed,
        emit_frontier_sdk_conductor_hop_admitted,
    )
    from services.git_integration_worker.cursor_sdk_ledger_hop import merge_hop_patch

    dispatch_id = str(row.get("dispatch_id") or "")
    thread_id = str(row.get("thread_id") or "")
    body = build_hop_team_dispatch_body(row, hop_reason_override="consult_harvest")
    if body is None:
        return False
    hop_seq = int(body.get("hop_seq") or 1)
    ok, detail = await post_conductor_hop_team_dispatch(body)
    from services.git_integration_worker.cursor_sdk_closeout.conductor_hop import (
        _load_row,
    )

    record_json = str((_load_row(dispatch_id) or row).get("record_json") or "")
    if ok:
        successor = str(detail.get("dispatch_id") or "") or str(
            detail.get("execution_id") or ""
        )
        if not successor:
            logger.warning(
                "consult pending continue admit ok but no successor id "
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
            data[_CONSULT_PENDING_CONTINUED_KEY] = time.time()
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
            hop_reason="consult_harvest",
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
        hop_reason="consult_harvest",
        error=error_text,
        status_code=detail.get("status_code"),
    )
    return False


def park_harvest_continue_owed(
    row: dict[str, Any],
    *,
    closeout_tokens: frozenset[str] | None = None,
    reply_fn: Any | None = None,
) -> bool:
    """R-B2 bind §6: PARKED_TRANSPORT terminal + Phase B fired + reply arrived.

    The same body grade as the wake continues when stored tokens omitted
    PARKED_TRANSPORT. A designed PARKED_TRANSPORT stamp is unchanged.
    """
    status = str(row.get("status") or "")
    if status not in ("completed", "failed", "cancelled"):
        return False
    tokens = closeout_tokens or _closeout_tokens_from_row(row)
    if _closeout_declares_done(row, tokens):
        return False
    body_park = _body_grades_park_harvest_owed(row)
    from services.git_integration_worker.cursor_sdk_closeout.conductor_exit_reasons import (
        row_pinned_stored_harvest,
    )

    pinned_harvest = row_pinned_stored_harvest(row)
    if "PARKED_TRANSPORT" not in tokens and not body_park and not pinned_harvest:
        return False
    rec = _record_data(row)
    if not harvest_owed_at_terminal(row):
        return False
    if rec.get(_HOP_PARK_HARVEST_CONTINUED_KEY):
        return False
    if _park_harvest_stop_claim_exhausted(row):
        return False
    if rec.get("hop_parked") or rec.get("park"):
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
    from services.git_integration_worker.cursor_sdk_closeout.conductor_hop import (
        _nested_harvest_head,
        _park_harvest_guard_text,
        conductor_has_live_nested,
        live_conductor_row_on_thread,
        mission_open_for_row,
    )

    head = _nested_harvest_head(row, rec)
    if head is not None and not head[1]:
        _maybe_record_park_harvest_continue_refused(
            row, predicate="nested_harvest_live", harvest_token=None
        )
        return False
    guard = _park_harvest_guard_text(row, rec)
    harvest_token = _park_harvest_target_token(guard)
    if harvest_token is None and not pinned_harvest:
        from services.git_integration_worker.cursor_sdk_closeout.conductor_hop import (
            _terminal_nested_producers_only,
        )

        if not _terminal_nested_producers_only(dispatch_id):
            _maybe_record_park_harvest_continue_refused(
                row, predicate="no_harvest_token", harvest_token=None
            )
            return False
        # Nest children are terminal and the closeout named no NEXT_ADMIT
        # harvest id. That is the continue. A later false return still stamps
        # a predicate (live nest, mission, budget).
        matched, refusal_predicate = True, None
    elif harvest_token is None and pinned_harvest:
        from services.git_integration_worker.cursor_sdk_closeout.conductor_exit_reasons import (
            prose_harvest_execution_id,
        )

        prose_token = prose_harvest_execution_id(str(rec.get("closeout_body") or ""))
        if prose_token is None:
            _maybe_record_park_harvest_continue_refused(
                row, predicate="no_harvest_token", harvest_token=None
            )
            return False
        harvest_token = prose_token
        matched, refusal_predicate = _evaluate_harvest_reply_execution(
            row, harvest_token
        )
    else:
        matched, refusal_predicate = _evaluate_harvest_reply_execution(
            row, harvest_token
        )
    if not matched:
        _maybe_record_park_harvest_continue_refused(
            row,
            predicate=refusal_predicate or "bounds_missing",
            harvest_token=harvest_token,
        )
        return False
    if reply_fn is not None and not reply_fn(
        thread_id, closeout_turn, "web-anthropic"
    ):
        _maybe_record_park_harvest_continue_refused(
            row, predicate="reply_absent", harvest_token=harvest_token
        )
        return False
    from services.git_integration_worker.cursor_sdk_closeout.conductor_hop_budget import (
        evaluate_hop_budget,
    )

    if live_conductor_row_on_thread(
        thread_id=thread_id, exclude_dispatch_id=dispatch_id
    ):
        _maybe_record_park_harvest_continue_refused(
            row, predicate="live_sibling_conductor", harvest_token=harvest_token
        )
        return False
    if conductor_has_live_nested(dispatch_id=dispatch_id):
        _maybe_record_park_harvest_continue_refused(
            row, predicate="live_nested", harvest_token=harvest_token
        )
        return False
    if not mission_open_for_row(row, closeout_tokens=tokens):
        _maybe_record_park_harvest_continue_refused(
            row, predicate="mission_closed", harvest_token=harvest_token
        )
        return False
    verdict = evaluate_hop_budget(row, closeout_tokens=tokens)
    if not verdict.ok or verdict.park:
        _maybe_record_park_harvest_continue_refused(
            row, predicate="budget", harvest_token=harvest_token
        )
        return False
    return True


async def fire_park_harvest_continue(row: dict[str, Any]) -> bool:
    """Admit park-harvest successor via hop path (bind §6 X-fresh)."""
    from services.git_integration_worker.cursor_sdk_closeout.conductor_hop import (
        _closeout_tokens_from_row,
        build_hop_team_dispatch_body,
        post_conductor_hop_team_dispatch,
    )
    from services.git_integration_worker.cursor_sdk_closeout.conductor_hop_budget import (
        evaluate_hop_budget,
    )
    from services.git_integration_worker.cursor_sdk_closeout.conductor_stop_service import (
        apply_hop_budget_announcer,
    )

    tokens = _closeout_tokens_from_row(row)
    verdict = evaluate_hop_budget(row, closeout_tokens=tokens)
    if not await apply_hop_budget_announcer(row, verdict):
        return False
    from services.git_integration_worker.cursor_sdk_hop_events import (
        emit_frontier_sdk_conductor_hop_admit_failed,
        emit_frontier_sdk_conductor_hop_admitted,
    )
    from services.git_integration_worker.cursor_sdk_ledger_hop import (
        merge_hop_admit_error_into_record_json_conn,
        merge_hop_patch,
        park_harvest_stop_not_claimed_exhausted,
    )

    dispatch_id = str(row.get("dispatch_id") or "")
    thread_id = str(row.get("thread_id") or "")
    if dispatch_id:
        ledger = CursorDispatchLedger.instance()
        with ledger._connect() as conn:
            fresh = conn.execute(
                "SELECT * FROM cursor_sdk_dispatches WHERE dispatch_id=?",
                (dispatch_id,),
            ).fetchone()
        if fresh is not None:
            row = {k: fresh[k] for k in fresh.keys()}
            thread_id = str(row.get("thread_id") or "")
    if _park_harvest_stop_claim_exhausted(row):
        return False
    body = build_hop_team_dispatch_body(row, hop_reason_override="park_harvest")
    if body is None:
        return False
    hop_seq = int(body.get("hop_seq") or 1)
    ok, detail = await post_conductor_hop_team_dispatch(body)
    record_json = str(row.get("record_json") or "")
    if ok:
        successor = str(detail.get("dispatch_id") or "") or str(
            detail.get("execution_id") or ""
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
    admit_patch: dict[str, Any] = {
        "error": error_text,
        "status_code": detail.get("status_code"),
    }
    failure_reason = detail.get("reason")
    if isinstance(failure_reason, str) and failure_reason:
        admit_patch["reason"] = failure_reason
    def _finalize_admit_err(merged_err: dict[str, Any]) -> dict[str, Any]:
        if park_harvest_stop_not_claimed_exhausted(merged_err):
            out = dict(merged_err)
            out["retryable"] = False
            return out
        return merged_err

    ledger = CursorDispatchLedger.instance()
    with ledger._connect() as conn:
        merge_hop_admit_error_into_record_json_conn(
            conn,
            dispatch_id=dispatch_id,
            patch=admit_patch,
            finalize=_finalize_admit_err,
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


def no_progress_producer_continue_owed(
    row: dict[str, Any],
    *,
    closeout_tokens: frozenset[str] | None = None,
) -> bool:
    """No-progress park whose linked producer has replied or gone terminal.

    Mission-cap, crash-cap, and admit-retry parks are not this predicate.
    A hop with no nest and no harvest id stays parked (true no-progress loop).
    """
    status = str(row.get("status") or "")
    if status not in ("completed", "failed", "cancelled"):
        return False
    tokens = closeout_tokens or _closeout_tokens_from_row(row)
    if tokens & (EXIT_PERSIST_STOPS | frozenset({"DONE"})):
        return False
    rec = _record_data(row)
    if rec.get("hop_parked") is not True:
        return False
    if str(rec.get("hop_park_reason") or "") != _PARK_REASON_NO_PROGRESS_CAP:
        return False
    released = rec.get("hop_park_released_at")
    if released is not None and released != "":
        return False
    if rec.get(_NO_PROGRESS_PRODUCER_CONTINUED_KEY):
        return False
    from services.git_integration_worker.cursor_sdk_closeout.conductor_hop import (
        _release_backoff_blocks,
    )

    if _release_backoff_blocks(row):
        return False
    from services.git_integration_worker.cursor_sdk_conductor_park_gate import (
        open_parks,
    )

    dispatch_id = str(row.get("dispatch_id") or "")
    ledger = CursorDispatchLedger.instance()
    with ledger._connect() as conn:
        parks = open_parks(
            conn,
            work_key=str(row.get("work_key") or "") or None,
            thread_id=str(row.get("thread_id") or "") or None,
            kinds=frozenset({"budget"}),
        )
    if [p.dispatch_id for p in parks] != [dispatch_id]:
        return False
    from services.git_integration_worker.cursor_sdk_park import _successor_admitted

    record_json = str(row.get("record_json") or "")
    with ledger._connect() as conn:
        if _successor_admitted(
            conn, predecessor_id=dispatch_id, record_json=record_json
        ):
            return False
    from services.git_integration_worker.cursor_sdk_closeout.conductor_hop import (
        linked_producer_disposition,
        live_conductor_row_on_thread,
        mission_open_for_row,
    )

    thread_id = str(row.get("thread_id") or "")
    if thread_id and live_conductor_row_on_thread(
        thread_id=thread_id, exclude_dispatch_id=dispatch_id
    ):
        return False
    if not mission_open_for_row(row, closeout_tokens=tokens):
        return False
    return linked_producer_disposition(row) == "ready"


def backfill_no_progress_claimed_successor(row: dict[str, Any]) -> bool:
    """Write ``hop_successor`` from a stop claim whose admit row already exists.

    A POST that committed and then lost the follow-up write leaves the claim
    held. The next sweep records that successor and does not post again.
    A claim whose admit row never landed is released so the retry can claim.
    """
    rec = _record_data(row)
    if rec.get("hop_parked") is not True:
        return False
    if str(rec.get("hop_park_reason") or "") != _PARK_REASON_NO_PROGRESS_CAP:
        return False
    if rec.get("hop_successor") or rec.get(_NO_PROGRESS_PRODUCER_CONTINUED_KEY):
        return False
    dispatch_id = str(row.get("dispatch_id") or "")
    if not dispatch_id:
        return False
    ledger = CursorDispatchLedger.instance()
    with ledger._connect() as conn:
        claimed = conn.execute(
            "SELECT serviced_admit FROM cursor_dispatch_stop_service WHERE stop_id=?",
            (dispatch_id,),
        ).fetchone()
        successor = str((claimed["serviced_admit"] if claimed else None) or "")
        landed = False
        if successor:
            landed = (
                conn.execute(
                    "SELECT 1 FROM cursor_sdk_dispatches WHERE dispatch_id=?",
                    (successor,),
                ).fetchone()
                is not None
            )
    if not successor:
        return False
    if not landed:
        ledger.release_stop_service(dispatch_id, successor)
        return False
    from services.git_integration_worker.cursor_sdk_conductor_park_gate import (
        HOP_PARK_RELEASED_AT_KEY,
    )

    now = time.time()
    patch: dict[str, Any] = {
        "hop_successor": successor,
        _NO_PROGRESS_PRODUCER_CONTINUED_KEY: now,
    }
    if rec.get(HOP_PARK_RELEASED_AT_KEY) in (None, ""):
        patch[HOP_PARK_RELEASED_AT_KEY] = now
    ledger.merge_record_json(dispatch_id=dispatch_id, patch=patch)
    return True


def _stamp_reply_deferral_when_harvest_live(row: dict[str, Any]) -> dict[str, Any]:
    """Stamp ``next_admit_blocked`` so a live harvest with a reply can lift.

    The reactor refuses the lift until the deferral exists. A no-progress
    park returns before that stamp, so the continue path writes it itself
    when the id is still live and the reply is what made the producer ready.
    """
    from services.git_integration_worker.cursor_sdk_closeout.conductor_hop import (
        HOP_DEFERRAL_GATE_KEY,
        SKIP_GATE_NEXT_ADMIT_BLOCKED,
        _harvest_target_token,
        _load_row,
        _named_target_is_terminal,
        _next_admit_guard_text,
        _record_data,
    )

    rec = _record_data(row)
    token = _harvest_target_token(_next_admit_guard_text(row, rec))
    if token is None:
        return row
    absent = str(rec.get(HOP_DEFERRAL_GATE_KEY) or "") == SKIP_GATE_NEXT_ADMIT_BLOCKED
    if absent or _named_target_is_terminal(token, absent_counts_terminal=absent):
        return row
    dispatch_id = str(row.get("dispatch_id") or "")
    if not dispatch_id:
        return row
    CursorDispatchLedger.instance().merge_record_json(
        dispatch_id=dispatch_id,
        patch={HOP_DEFERRAL_GATE_KEY: SKIP_GATE_NEXT_ADMIT_BLOCKED},
    )
    return _load_row(dispatch_id) or row


async def fire_no_progress_producer_continue(row: dict[str, Any]) -> bool:
    """Release one no-progress park and admit a harvest successor. No re-fire."""
    from services.git_integration_worker.cursor_sdk_closeout.conductor_hop import (
        build_hop_team_dispatch_body,
        post_conductor_hop_team_dispatch,
    )
    from services.git_integration_worker.cursor_sdk_conductor_park_gate import (
        HOP_PARK_RELEASED_AT_KEY,
    )
    from services.git_integration_worker.cursor_sdk_hop_events import (
        emit_frontier_sdk_conductor_hop_admit_failed,
        emit_frontier_sdk_conductor_hop_admitted,
    )

    row = _stamp_reply_deferral_when_harvest_live(row)
    dispatch_id = str(row.get("dispatch_id") or "")
    thread_id = str(row.get("thread_id") or "")
    body = build_hop_team_dispatch_body(
        row, hop_reason_override=HOP_REASON_PRODUCER_HARVEST
    )
    if body is None:
        return False
    hop_seq = int(body.get("hop_seq") or 1)
    ok, detail = await post_conductor_hop_team_dispatch(body)
    ledger = CursorDispatchLedger.instance()
    if ok:
        successor = str(detail.get("dispatch_id") or "") or str(
            detail.get("execution_id") or ""
        )
        if not successor:
            logger.warning(
                "no-progress producer continue admit ok but no successor id "
                "dispatch_id=%s detail=%s",
                dispatch_id,
                detail,
            )
            return False
        # merge, not a full replace: the admit transaction already wrote
        # hop_park_released_at, and a stale snapshot would drop it.
        released_at = time.time()
        ledger.merge_record_json(
            dispatch_id=dispatch_id,
            patch={
                "hop_successor": successor,
                _NO_PROGRESS_PRODUCER_CONTINUED_KEY: released_at,
                HOP_PARK_RELEASED_AT_KEY: released_at,
            },
        )
        emit_frontier_sdk_conductor_hop_admitted(
            predecessor_dispatch_id=dispatch_id,
            successor_dispatch_id=successor,
            thread_id=thread_id,
            hop_seq=hop_seq,
            hop_reason=HOP_REASON_PRODUCER_HARVEST,
        )
        return True
    if detail.get(
        "reason"
    ) == "stop_not_claimed" and backfill_no_progress_claimed_successor(row):
        return True
    error_text = json.dumps(detail, sort_keys=True)[:500]
    ledger.merge_record_json(
        dispatch_id=dispatch_id,
        patch={
            "hop_admit_error": {
                "error": error_text,
                "status_code": detail.get("status_code"),
            }
        },
    )
    emit_frontier_sdk_conductor_hop_admit_failed(
        dispatch_id=dispatch_id,
        thread_id=thread_id,
        hop_seq=hop_seq,
        hop_reason=HOP_REASON_PRODUCER_HARVEST,
        error=error_text,
        status_code=detail.get("status_code"),
    )
    return False


__all__ = [
    "backfill_no_progress_claimed_successor",
    "build_park_harvest_arm_recipe",
    "consult_pending_continue_owed",
    "fire_consult_pending_continue",
    "fire_park_harvest",
    "fire_no_progress_producer_continue",
    "fire_park_harvest_continue",
    "maybe_fire_conductor_park_harvest",
    "no_progress_producer_continue_owed",
    "park_harvest_continue_owed",
    "park_harvest_owed",
    "reply_arrived_on_thread",
]
