"""Stall-revoke reconciler — Route A succession honesty for hop cadence (arc 6928).

On joinable ``cdp.generate.stalled``, revoke the pending succession claim recorded
at ``mark_hop_fired`` and apply failure cooldown + optional breaker.
"""

from __future__ import annotations

import json
import os
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from claude_bundles.hop_cadence_id_map import (
    claim_join_keys,
    normalize_id,
    stall_matches_claim,
    submitted_updates_claim,
)
from claude_bundles.hop_seat_cutover import (
    matched_active_work_row,
    successor_confirm_active,
)
from transport_utils import EVENTS_QUERY_SOCK, make_sync_client
from universal_logging import get_logger

from services.git_integration_worker.cursor_auto.hop_cadence_events import (
    emit_registration_advanced,
    emit_revoke_breaker,
    emit_seat_rebound,
    emit_succession_confirmed,
    emit_succession_revoked,
)
from services.git_integration_worker.cursor_auto.hop_cadence_predecessor import (
    PredecessorConfirmError,
    PredecessorHandle,
    PredecessorVerdict,
    non_holder_handles,
    prior_registration_for_confirm,
    predecessor_for_confirm,
)
from services.git_integration_worker.cursor_auto.hop_cadence_succession_release import (
    RELEASE_IDLE_STREAK_REQUIRED,
    release_superseded_on_confirm,
)
from services.git_integration_worker.cursor_auto.hop_cadence_watch import (
    advance_registration_on_confirm,
    load_watches,
    save_watches,
)

logger = get_logger(__name__)

# Floor 15s: observed chip_missing stall lag 4–14s (Q1/Q2 sidecar, n=15 class).
STALL_OBSERVE_FLOOR_S = 15.0
# Upper bound for stall→claim join after hop fire (generous vs day stall tail).
STALL_JOIN_MAX_AGE_S = 600.0
REVOKE_BREAKER_N = 3
# Outstanding release obligations retry until terminalized or this cap — prevents silent drift.
MAX_RELEASE_OBLIGATION_RETRIES = 12
_RELEASE_OBLIGATION_PENDING = "pending"
_RELEASE_OBLIGATION_RELEASED = "released"
_RELEASE_OBLIGATION_FAILED = "failed"
_STATE_FILENAME = "hop_cadence_reconcile_state.json"
_GENERATE_SIGNALS = (
    "cdp.generate.stalled",
    "cdp.generate.submitted",
    "cdp.generate.proof",
)
_EVENTS_QUERY_URL = f"unix://{EVENTS_QUERY_SOCK}"


def reconcile_state_path() -> Path:
    """Durable cursor for last Event Service seq scanned."""
    return Path.home() / ".gateway" / "cdp-registry" / _STATE_FILENAME


def load_reconcile_state(path: Path | None = None) -> dict[str, Any]:
    """Load durable reconcile cursor; default ``last_seq`` is zero when absent."""
    target = path or reconcile_state_path()
    if not target.is_file():
        return {"last_seq": 0}
    try:
        raw = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"last_seq": 0}
    return raw if isinstance(raw, dict) else {"last_seq": 0}


def save_reconcile_state(state: dict[str, Any], path: Path | None = None) -> None:
    """Persist reconcile cursor atomically beside the CDP registry store."""
    target = path or reconcile_state_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(target)


def _parse_payload(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def query_generate_events_since(
    since_seq: int,
    *,
    limit: int = 200,
    query_fn: Callable[[str, list[Any], int], list[dict[str, Any]]] | None = None,
) -> list[dict[str, Any]]:
    """Return ``cdp.generate.{stalled,submitted,proof}`` rows with seq > since_seq."""
    placeholders = ",".join("?" for _ in _GENERATE_SIGNALS)
    sql = (
        "SELECT seq, signal, ts_unix_ms, execution_id, payload "
        f"FROM events WHERE seq > ? AND signal IN ({placeholders}) "
        "ORDER BY seq ASC LIMIT ?"
    )
    params: list[Any] = [since_seq, *_GENERATE_SIGNALS, limit]
    if query_fn is not None:
        rows = query_fn(sql, params, limit)
    else:
        rows = _default_event_query(sql, params, limit)
    for row in rows:
        row["payload"] = _parse_payload(row.get("payload"))
    return rows


def _default_event_query(sql: str, params: list[Any], limit: int) -> list[dict[str, Any]]:
    sock = os.environ.get("EVENTS_QUERY_SOCK", EVENTS_QUERY_SOCK)
    if not os.path.exists(sock):
        return []
    try:
        with make_sync_client(_EVENTS_QUERY_URL, timeout=10.0) as client:
            resp = client.post(
                "/v1/query",
                json={"type": "sql", "sql": sql, "params": params, "limit": limit},
            )
            if resp.status_code != 200:
                return []
            payload = resp.json()
    except Exception as exc:  # noqa: BLE001 — reconcile must not crash cadence loop
        logger.warning("hop_cadence event query failed: %s", exc)
        return []
    rows = payload.get("rows") if isinstance(payload, dict) else None
    return list(rows) if isinstance(rows, list) else []


def record_succession_claim(
    row: dict[str, Any],
    *,
    execution_id: str | None,
    satellite_execution_id: str | None = None,
    now: float,
) -> dict[str, Any]:
    """Attach a pending succession claim before the post-hop seated_at reset."""
    updated = dict(row)
    pre_seated = updated.get("seated_at")
    if pre_seated is not None:
        try:
            updated["pre_hop_seated_at"] = float(pre_seated)
        except (TypeError, ValueError):
            pass
    exec_id = normalize_id(execution_id)
    sat_id = normalize_id(satellite_execution_id)
    updated["succession_status"] = "pending"
    updated["pending_succession"] = {
        "execution_id": exec_id,
        "satellite_execution_id": sat_id,
        "claimed_at": now,
        "observe_floor_s": STALL_OBSERVE_FLOOR_S,
        "join_max_age_s": STALL_JOIN_MAX_AGE_S,
    }
    if exec_id:
        updated["pending_execution_id"] = exec_id
    if sat_id:
        updated["pending_satellite_execution_id"] = sat_id
    return updated


def revoke_succession_claim(
    row: dict[str, Any],
    *,
    stall_payload: dict[str, Any],
    now: float,
) -> dict[str, Any]:
    """Revoke pending claim, restore pre-hop age source, apply failure cooldown."""
    updated = dict(row)
    pending = updated.get("pending_succession")
    exec_id = normalize_id(
        stall_payload.get("execution_id")
        or (pending.get("execution_id") if isinstance(pending, dict) else None)
    )
    pre_seated = updated.get("pre_hop_seated_at")
    if pre_seated is not None:
        try:
            updated["seated_at"] = float(pre_seated)
        except (TypeError, ValueError):
            pass
    updated["succession_status"] = "revoked"
    updated["last_succession_failure_at"] = now
    updated["last_hop_at"] = now
    updated.pop("successor_execution_id", None)
    updated.pop("pending_succession", None)
    updated.pop("pending_execution_id", None)
    updated.pop("pending_satellite_execution_id", None)
    count = int(updated.get("revocation_count") or 0) + 1
    updated["revocation_count"] = count
    updated["last_revoke"] = {
        "execution_id": exec_id,
        "stall_stage": stall_payload.get("stall_stage"),
        "error": stall_payload.get("error"),
        "revoked_at": now,
    }
    if count >= REVOKE_BREAKER_N:
        updated["breaker_tripped"] = True
        updated["breaker_tripped_at"] = now
    return updated


def confirm_succession_claim(row: dict[str, Any], *, now: float) -> dict[str, Any]:
    """Clear pending state on proof — live seat path (Control A)."""
    updated = dict(row)
    updated["succession_status"] = "confirmed"
    updated.pop("pending_succession", None)
    updated.pop("pending_execution_id", None)
    updated.pop("pending_satellite_execution_id", None)
    updated["succession_confirmed_at"] = now
    return updated


def breaker_blocks_hop(row: dict[str, Any]) -> bool:
    """Return True when repeated stall revocations tripped the cadence breaker."""
    return bool(row.get("breaker_tripped"))


def _claim_within_join_window(row: dict[str, Any], *, now: float) -> bool:
    pending = row.get("pending_succession")
    if not isinstance(pending, dict):
        return False
    claimed_at = pending.get("claimed_at")
    if claimed_at is None:
        return True
    try:
        age = now - float(claimed_at)
    except (TypeError, ValueError):
        return True
    max_age = float(pending.get("join_max_age_s") or STALL_JOIN_MAX_AGE_S)
    return age <= max_age


def apply_event_to_watch(
    row: dict[str, Any],
    event: dict[str, Any],
    *,
    now: float,
) -> tuple[dict[str, Any], str | None]:
    """Mutate one watch row for a single generate lifecycle event; return action label."""
    signal = str(event.get("signal") or "")
    payload = event.get("payload")
    if not isinstance(payload, dict):
        payload = _parse_payload(payload)

    if signal == "cdp.generate.submitted":
        sat_id = submitted_updates_claim(payload, row)
        if sat_id:
            updated = dict(row)
            pending = dict(updated.get("pending_succession") or {})
            pending["satellite_execution_id"] = sat_id
            updated["pending_succession"] = pending
            updated["pending_satellite_execution_id"] = sat_id
            return updated, "satellite_attached"
        return row, None

    if signal == "cdp.generate.proof":
        if row.get("succession_status") != "pending":
            return row, None
        proof_exec = normalize_id(payload.get("execution_id"))
        if proof_exec and proof_exec in claim_join_keys(row):
            return confirm_succession_claim(row, now=now), "confirmed"
        return row, None

    if signal == "cdp.generate.stalled":
        if row.get("succession_status") != "pending":
            return row, None
        if not _claim_within_join_window(row, now=now):
            return row, None
        if not stall_matches_claim(payload, row):
            return row, None
        return revoke_succession_claim(row, stall_payload=payload, now=now), "revoked"

    return row, None


def reconcile_stall_revocations(
    *,
    watches_path: Path | None = None,
    state_path: Path | None = None,
    now: float | None = None,
    query_fn: Callable[[str, list[Any], int], list[dict[str, Any]]] | None = None,
) -> dict[str, Any]:
    """Scan Event Service for generate terminal events and reconcile watch ledger."""
    ts = time.time() if now is None else now
    state = load_reconcile_state(state_path)
    since_seq = int(state.get("last_seq") or 0)
    events = query_generate_events_since(since_seq, query_fn=query_fn)
    watches = load_watches(watches_path)
    actions: list[dict[str, Any]] = []
    max_seq = since_seq

    for event in events:
        seq = int(event.get("seq") or 0)
        if seq > max_seq:
            max_seq = seq
        payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
        for thread_id, row in list(watches.items()):
            updated, action = apply_event_to_watch(row, event, now=ts)
            if action is None:
                continue
            watches[thread_id] = updated
            actions.append(
                {
                    "thread_id": thread_id,
                    "action": action,
                    "signal": event.get("signal"),
                    "seq": seq,
                }
            )
            if action == "revoked":
                exec_id = normalize_id(payload.get("execution_id")) or ""
                count = int(updated.get("revocation_count") or 0)
                emit_succession_revoked(
                    thread_id=thread_id,
                    execution_id=exec_id,
                    stall_stage=payload.get("stall_stage"),
                    revocation_count=count,
                )
                if updated.get("breaker_tripped"):
                    emit_revoke_breaker(
                        thread_id=thread_id,
                        revocation_count=count,
                        breaker_n=REVOKE_BREAKER_N,
                    )

    if watches:
        save_watches(watches, watches_path)
    if max_seq > since_seq:
        state["last_seq"] = max_seq
        save_reconcile_state(state, state_path)

    return {"actions": actions, "events_scanned": len(events), "last_seq": max_seq}


def _handle_from_release_obligation(
    obligation: dict[str, Any],
    row: dict[str, Any],
) -> PredecessorHandle | None:
    exec_id = str(obligation.get("execution_id") or row.get("superseded_execution_id") or "").strip()
    reg_id = str(
        obligation.get("registration_id") or row.get("superseded_registration_id") or ""
    ).strip()
    verdict_raw = str(
        obligation.get("verdict") or row.get("predecessor_verdict") or ""
    ).strip()
    if not exec_id:
        return None
    try:
        verdict = PredecessorVerdict(verdict_raw)
    except ValueError:
        verdict = PredecessorVerdict.INCUMBENT_RECORDED
    return PredecessorHandle(
        registration_id=reg_id,
        execution_id=exec_id,
        verdict=verdict,
    )


def persist_release_obligation(
    row: dict[str, Any],
    handle: PredecessorHandle,
    outcome: dict[str, Any],
    *,
    now: float,
) -> dict[str, Any]:
    """Record an outstanding release obligation when release deferred or errored."""
    updated = dict(row)
    prior = updated.get("release_obligation")
    prior_dict = prior if isinstance(prior, dict) else {}
    retry_count = int(prior_dict.get("retry_count") or 0)
    if prior_dict.get("status") == _RELEASE_OBLIGATION_PENDING:
        retry_count += 1
    streak = int(outcome.get("idle_streak") or prior_dict.get("idle_streak") or 0)
    if outcome.get("action") == "deferred" and outcome.get("reason") != "predecessor_idle_streak_unsatisfied":
        streak = 0
    updated["release_obligation"] = {
        "execution_id": handle.execution_id,
        "registration_id": handle.registration_id,
        "verdict": handle.verdict.value,
        "status": _RELEASE_OBLIGATION_PENDING,
        "retry_count": retry_count,
        "idle_streak": streak,
        "idle_streak_required": int(
            outcome.get("idle_streak_required") or RELEASE_IDLE_STREAK_REQUIRED
        ),
        "created_at": float(prior_dict.get("created_at") or now),
        "last_attempt_at": now,
        "last_action": outcome.get("action"),
        "last_reason": outcome.get("reason") or outcome.get("error"),
        "last_abort_outcome": outcome.get("abort_outcome"),
    }
    if retry_count >= MAX_RELEASE_OBLIGATION_RETRIES:
        updated["release_obligation"]["status"] = _RELEASE_OBLIGATION_FAILED
        updated["release_obligation"]["failed_at"] = now
        updated["release_obligation"]["failure_reason"] = "max_retries_exhausted"
    return updated


def clear_release_obligation(row: dict[str, Any], *, terminal_status: str) -> dict[str, Any]:
    """Clear or terminalize a release obligation after successful release."""
    updated = dict(row)
    prior = updated.get("release_obligation")
    prior_dict = prior if isinstance(prior, dict) else {}
    updated["release_obligation"] = {
        **prior_dict,
        "status": terminal_status,
        "cleared_at": prior_dict.get("last_attempt_at"),
    }
    if terminal_status == _RELEASE_OBLIGATION_RELEASED:
        updated.pop("release_obligation", None)
    return updated


def apply_release_outcome_to_row(
    row: dict[str, Any],
    handle: PredecessorHandle,
    outcome: dict[str, Any],
    *,
    now: float,
) -> dict[str, Any]:
    """Update watch row obligation fields from a release attempt outcome."""
    action = outcome.get("action")
    if action == "terminalized":
        return clear_release_obligation(row, terminal_status=_RELEASE_OBLIGATION_RELEASED)
    if action == "already_terminal":
        return clear_release_obligation(row, terminal_status=_RELEASE_OBLIGATION_RELEASED)
    if action in {"deferred", "error"}:
        return persist_release_obligation(row, handle, outcome, now=now)
    return row


def _call_release(
    release_fn: Callable[..., dict[str, Any]],
    handle: PredecessorHandle,
    *,
    idle_streak: int = 0,
) -> dict[str, Any]:
    try:
        return release_fn(handle, idle_streak=idle_streak)
    except TypeError:
        return release_fn(handle)


def reconcile_release_obligations(
    watches: dict[str, Any],
    *,
    now: float,
    release_fn: Callable[..., dict[str, Any]],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Retry outstanding release obligations without re-confirming succession."""
    retries: list[dict[str, Any]] = []
    changed = False
    for thread_id, row in list(watches.items()):
        obligation = row.get("release_obligation")
        if not isinstance(obligation, dict):
            continue
        if obligation.get("status") != _RELEASE_OBLIGATION_PENDING:
            continue
        handle = _handle_from_release_obligation(obligation, row)
        if handle is None:
            continue
        streak = int(obligation.get("idle_streak") or 0)
        outcome = _call_release(release_fn, handle, idle_streak=streak)
        updated = apply_release_outcome_to_row(row, handle, outcome, now=now)
        if updated != row:
            watches[thread_id] = updated
            changed = True
        retries.append({"thread_id": thread_id, **outcome})
    return watches, retries


def reconcile_succession_confirmations(
    *,
    watches_path: Path | None = None,
    now: float | None = None,
    snapshot_reader: Callable[[], dict[str, Any]] | None = None,
    release_fn: Callable[[PredecessorHandle], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Observe live active-work membership and advance watch registration ids once."""
    ts = time.time() if now is None else now
    try:
        snap = (snapshot_reader or _default_snapshot_reader)()
    except Exception as exc:  # noqa: BLE001 — reconcile must not crash cadence loop
        logger.warning("hop_cadence confirm snapshot failed: %s", exc)
        return {"confirmations": [], "error": str(exc)}
    if not isinstance(snap, dict):
        snap = {}
    watches = load_watches(watches_path)
    confirmations: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    releases: list[dict[str, Any]] = []
    obligation_retries: list[dict[str, Any]] = []
    release = release_fn or release_superseded_on_confirm
    changed = False

    watches, obligation_retries = reconcile_release_obligations(
        watches,
        now=ts,
        release_fn=release,
    )
    if obligation_retries:
        changed = True
        releases.extend(obligation_retries)

    for thread_id, row in list(watches.items()):
        if not successor_confirm_active(row, snap):
            continue
        matched_key, aw_row = matched_active_work_row(row, snap)
        if not matched_key:
            continue
        handle = predecessor_for_confirm(row, snap)
        if isinstance(handle, PredecessorConfirmError):
            logger.error(
                "hop_cadence confirm predecessor error thread=%s reason=%s detail=%s",
                thread_id,
                handle.reason,
                handle.detail,
            )
            errors.append(
                {
                    "thread_id": thread_id,
                    "reason": handle.reason,
                    "detail": handle.detail,
                }
            )
            continue
        if (
            handle.verdict == PredecessorVerdict.INCUMBENT_RECORDED
            and not str(row.get("superseded_execution_id") or "").strip()
        ):
            row = dict(row)
            row.update(handle.as_watch_fields())
            watches[thread_id] = row
            changed = True
        prior_reg = prior_registration_for_confirm(handle)
        updated, transition = advance_registration_on_confirm(
            row,
            matched_key=matched_key,
            active_work_row=aw_row,
            now=ts,
            prior_registration_id=prior_reg,
        )
        if transition is None:
            continue
        watches[thread_id] = updated
        changed = True
        _, new_reg = transition
        watch_reg = str(row.get("registration_id") or "")
        superseded_exec = handle.execution_id
        emit_succession_confirmed(
            thread_id=thread_id,
            matched_key=matched_key,
            watch_registration_id=watch_reg,
            prior_registration_id=prior_reg,
            superseded_execution_id=superseded_exec,
        )
        release_outcome = _call_release(release, handle)
        releases.append({"thread_id": thread_id, **release_outcome})
        updated = apply_release_outcome_to_row(updated, handle, release_outcome, now=ts)
        watches[thread_id] = updated
        holder_exec = str((aw_row or {}).get("execution_id") or matched_key)
        for extra in non_holder_handles(
            snap, thread_id=thread_id, holder_execution_id=holder_exec
        ):
            if extra.execution_id == handle.execution_id:
                continue
            extra_outcome = _call_release(release, extra)
            releases.append({"thread_id": thread_id, **extra_outcome})
        if new_reg:
            emit_registration_advanced(
                thread_id=thread_id,
                prior_registration_id=prior_reg,
                new_registration_id=new_reg,
                superseding_execution_id=matched_key,
                superseded_execution_id=superseded_exec,
            )
            emit_seat_rebound(
                thread_id=thread_id,
                prior_registration_id=prior_reg,
                new_registration_id=new_reg,
                superseded_execution_id=superseded_exec,
            )
        confirmations.append(
            {
                "thread_id": thread_id,
                "matched_key": matched_key,
                "prior_registration_id": prior_reg,
                "superseded_execution_id": superseded_exec,
                "new_registration_id": new_reg,
            }
        )
    if changed:
        save_watches(watches, watches_path)
    return {
        "confirmations": confirmations,
        "errors": errors,
        "releases": releases,
        "obligation_retries": obligation_retries,
    }


def _default_snapshot_reader() -> dict[str, Any]:
    from services.git_integration_worker.cursor_auto.cdp_escalation import (
        read_cdp_lane_snapshot,
    )

    return read_cdp_lane_snapshot()


__all__ = [
    "MAX_RELEASE_OBLIGATION_RETRIES",
    "REVOKE_BREAKER_N",
    "STALL_JOIN_MAX_AGE_S",
    "STALL_OBSERVE_FLOOR_S",
    "apply_event_to_watch",
    "apply_release_outcome_to_row",
    "breaker_blocks_hop",
    "clear_release_obligation",
    "confirm_succession_claim",
    "persist_release_obligation",
    "query_generate_events_since",
    "reconcile_release_obligations",
    "reconcile_stall_revocations",
    "reconcile_succession_confirmations",
    "record_succession_claim",
    "revoke_succession_claim",
]
