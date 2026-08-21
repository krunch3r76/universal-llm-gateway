"""Hop-cadence watch ledger — enroll, age, and evaluate operator CSE seats.

Owned by cursor-auto (not the CDP seat). Persists beside the CDP registry so
watches survive GIW restart. Callers: ``hop_cadence`` fire path and enqueue
observe hook. Prefer registry ``started_at`` when ``active.json`` has the row;
otherwise age from first Auto observe (``seated_at``).
"""

from __future__ import annotations

import json
import os
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agent_seat.registry import normalize_bus_address
from claude_bundles.cdp_registry_store import load_active
from hop_handoff import (
    StandingHandoffFreshness,
    assess_standing_handoff,
    standing_handoff_path,
    standing_handoff_uri,
)
from hop_handoff import (
    cse_age_threshold_s as age_threshold_s,
)
from universal_logging import get_logger

from services.git_integration_worker.cursor_auto.hop_cadence_home_lane import (
    home_lane_from_mailbox,
    watch_thread_for_job,
)
from services.git_integration_worker.cursor_auto.hop_cadence_lane_gate import (
    hop_cadence_lane_skip_reason,
)
from services.git_integration_worker.cursor_auto.queue import AutoJob

logger = get_logger(__name__)

# Re-exports from hop_handoff so existing GIW imports keep resolving.
__all__ = (
    "HopDecision",
    "StandingHandoffFreshness",
    "age_threshold_s",
    "assess_standing_handoff",
    "standing_handoff_path",
    "standing_handoff_uri",
)

# Operator bind 2026-08-12 (arc 7119): cadence = 30 minutes wall-clock.
_DEFAULT_COOLDOWN_S = 1800.0
_DEFAULT_SCAN_INTERVAL_S = 30.0
_WATCH_FILENAME = "hop_cadence_watches.json"


def cooldown_s() -> float:
    """Seconds after a cadence hop before the same lane may hop again.

    Override with env ``CURSOR_AUTO_HOP_COOLDOWN_S`` (minimum 60s).
    """
    raw = os.environ.get("CURSOR_AUTO_HOP_COOLDOWN_S", "").strip()
    if not raw:
        return _DEFAULT_COOLDOWN_S
    try:
        return max(60.0, float(raw))
    except ValueError:
        return _DEFAULT_COOLDOWN_S


def scan_interval_s() -> float:
    """Background loop sleep between watch evaluations.

    Override with env ``CURSOR_AUTO_HOP_SCAN_S`` (minimum 5s).
    """
    raw = os.environ.get("CURSOR_AUTO_HOP_SCAN_S", "").strip()
    if not raw:
        return _DEFAULT_SCAN_INTERVAL_S
    try:
        return max(5.0, float(raw))
    except ValueError:
        return _DEFAULT_SCAN_INTERVAL_S


def watches_path() -> Path:
    """Durable watch ledger path beside the CDP registry store.

    Override with env ``CURSOR_AUTO_HOP_WATCHES_PATH`` (non-empty) for tests.
    """
    raw = os.environ.get("CURSOR_AUTO_HOP_WATCHES_PATH", "").strip()
    if raw:
        return Path(raw)
    return Path.home() / ".gateway" / "cdp-registry" / _WATCH_FILENAME


@dataclass(frozen=True)
class HopDecision:
    """One evaluate() outcome for a watched private lane."""

    thread_id: str
    action: str  # fire | skip
    reason: str
    age_s: float | None = None
    threshold_s: float | None = None
    signal: str | None = None
    handoff: StandingHandoffFreshness | None = None


def load_watches(path: Path | None = None) -> dict[str, dict[str, Any]]:
    """Load the hop-cadence watch ledger; empty dict on missing/corrupt file."""
    target = path or watches_path()
    if not target.is_file():
        return {}
    try:
        raw = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("hop_cadence watch load failed path=%s err=%s", target, exc)
        return {}
    if not isinstance(raw, dict):
        return {}
    out: dict[str, dict[str, Any]] = {}
    for key, row in raw.items():
        if isinstance(row, dict):
            out[str(key)] = dict(row)
    return out


def save_watches(watches: dict[str, dict[str, Any]], path: Path | None = None) -> None:
    """Atomically persist the watch ledger (tmp + replace)."""
    target = path or watches_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(".tmp")
    tmp.write_text(
        json.dumps(watches, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    tmp.replace(target)


def _is_operator_proxy_mailbox(from_agent: str) -> bool:
    """True for mailboxes that own a private-lane operator CSE seat.

    Historical enroll path keyed on ``web-*``. Live operator-proxy seats
    post as ``cdp-operator-*`` (observed 6655 turn 2866). Both must enroll —
    otherwise a cull of the watch ledger never re-heals on the standing lane.
    """
    addr = normalize_bus_address((from_agent or "").strip())
    return addr.startswith("web-") or addr.startswith("cdp-operator-")


# Compat alias — tests / callers that still import the old name.
_is_web_mailbox = _is_operator_proxy_mailbox


def should_observe_job(job: AutoJob) -> bool:
    """True when an inbound Auto job should enroll/refresh a hop watch."""
    if job.continuity_hop:
        return False
    if (job.continuity_matched_token or "") == "cadence:auto":
        return False
    subject = (job.subject or "").lower()
    if "hop cadence" in subject and "cursor-auto" in subject:
        return False
    return _is_operator_proxy_mailbox(job.from_agent)


def registry_started_at(registration_id: str | None) -> float | None:
    """Return ``active.json`` started_at for a live registration, else None."""
    rid = (registration_id or "").strip()
    if not rid:
        return None
    active = load_active()
    row = active.get(rid)
    if not isinstance(row, dict):
        return None
    if row.get("status") not in ("active", "orphaned_alive", "allocating"):
        return None
    started = row.get("started_at")
    if started is None:
        return None
    try:
        return float(started)
    except (TypeError, ValueError):
        return None


def observe_lane_from_enqueue(
    job: AutoJob, *, now: float | None = None, path: Path | None = None
) -> dict[str, Any] | None:
    """Enroll or refresh a hop watch from a web-* Auto enqueue (writes disk).

    ``cdp-operator-{lane}-*`` jobs key the standing private lane, not
    ``job.thread_id``. Work-thread posts refresh that lane only.
    """
    if not should_observe_job(job):
        return None
    ts = time.time() if now is None else now
    watches = load_watches(path)
    job_thread = str(job.thread_id)
    thread_id = watch_thread_for_job(job)
    lane_skip = hop_cadence_lane_skip_reason(thread_id)
    if lane_skip:
        logger.info(
            "hop_cadence observe skip thread=%s reason=%s aliased_from=%s",
            thread_id,
            lane_skip,
            job_thread if job_thread != thread_id else "",
        )
        return None
    aliased = job_thread != thread_id
    row = dict(watches.get(thread_id) or {})
    seated = row.get("seated_at")
    if seated is None:
        if aliased:
            row["seated_at"] = ts
            row["enroll_source"] = "first_auto_observe"
        else:
            reg_started = registry_started_at(job.cse_registration_id)
            row["seated_at"] = float(reg_started) if reg_started is not None else ts
            row["enroll_source"] = (
                "registry_started_at"
                if reg_started is not None
                else "first_auto_observe"
            )
    row["thread_id"] = thread_id
    row["last_seen_at"] = ts
    row["from_agent"] = normalize_bus_address(job.from_agent)
    if not aliased:
        if job.cse_registration_id:
            row["registration_id"] = job.cse_registration_id
        chat_url = (job.cse_chat_url or "").strip() or None
        if not chat_url and job.cse_registration_id:
            from claude_bundles.cdp_registry import chat_url_for_registration

            chat_url = chat_url_for_registration(job.cse_registration_id)
        if chat_url:
            row["chat_url"] = chat_url
    row["purpose"] = "operator-proxy"
    if not row.get("mission"):
        from services.git_integration_worker.cursor_auto.hop_cadence_mission import (
            mission_candidate_from_job,
        )

        candidate = mission_candidate_from_job(job)
        if candidate:
            row["mission"] = candidate
    watches[thread_id] = row
    save_watches(watches, path)
    logger.info(
        "hop_cadence observe thread=%s seated_at=%s age_s=%.1f aliased_from=%s",
        thread_id,
        row.get("seated_at"),
        ts - float(row["seated_at"]),
        job_thread if aliased else "",
    )
    return row


def effective_seated_at(row: dict[str, Any]) -> float | None:
    """Age source for cadence evaluate — post-hop seated_at beats stale registry."""
    from claude_bundles.hop_seat_cutover import effective_seated_at_after_hop

    return effective_seated_at_after_hop(row, registry_started_at=registry_started_at)


def evaluate_watch(
    row: dict[str, Any],
    *,
    now: float | None = None,
    threshold: float | None = None,
    cool: float | None = None,
    in_flight_probe: Callable[[str], bool] | None = None,
    standdown_probe: Callable[[str], bool] | None = None,
) -> HopDecision:
    """Decide fire/skip for one watch row; does not mutate the ledger.

    *in_flight_probe* is lane-keyed (thread id → running?). Absent probe
    means no in-flight inhibit — production ``scan_and_fire`` injects one.
    *standdown_probe* is lane-keyed (thread id → open stand-down ACK?).
    Absent probe means no stand-down inhibit — production ``scan_and_fire``
    injects one.
    """
    ts = time.time() if now is None else now
    thr = age_threshold_s() if threshold is None else threshold
    cd = cooldown_s() if cool is None else cool
    thread_id = str(row.get("thread_id") or "")
    if not thread_id:
        return HopDecision("", "skip", "missing_thread_id")
    lane_skip = hop_cadence_lane_skip_reason(thread_id)
    if lane_skip:
        logger.info(
            "hop_cadence evaluate skip thread=%s reason=%s",
            thread_id,
            lane_skip,
        )
        return HopDecision(
            thread_id,
            "skip",
            lane_skip,
            threshold_s=thr,
            signal=lane_skip,
        )
    home = home_lane_from_mailbox(str(row.get("from_agent") or ""))
    if home and home != thread_id:
        logger.info(
            "hop_cadence evaluate skip thread=%s reason=not_home_lane home=%s",
            thread_id,
            home,
        )
        return HopDecision(
            thread_id, "skip", "not_home_lane", threshold_s=thr, signal="not_home_lane"
        )
    from services.git_integration_worker.cursor_auto.hop_cadence_stall_reconcile import (
        breaker_blocks_hop,
    )

    if breaker_blocks_hop(row):
        return HopDecision(
            thread_id, "skip", "revoke_breaker", threshold_s=thr, signal="breaker"
        )
    last_hop = row.get("last_hop_at")
    if last_hop is not None:
        try:
            if ts - float(last_hop) < cd:
                return HopDecision(
                    thread_id, "skip", "cooldown", threshold_s=thr, signal="cooldown"
                )
        except (TypeError, ValueError):
            pass
    if in_flight_probe is not None and in_flight_probe(thread_id):
        return HopDecision(
            thread_id,
            "skip",
            "in_flight_commission",
            threshold_s=thr,
            signal="in_flight_commission",
        )
    from services.git_integration_worker.cursor_auto.hop_cadence_standdown import (
        STANDDOWN_ACK_OPEN_REASON,
    )

    if standdown_probe is not None and standdown_probe(thread_id):
        return HopDecision(
            thread_id,
            "skip",
            STANDDOWN_ACK_OPEN_REASON,
            threshold_s=thr,
            signal=STANDDOWN_ACK_OPEN_REASON,
        )
    seated = effective_seated_at(row)
    if seated is None:
        return HopDecision(thread_id, "skip", "no_seated_at", threshold_s=thr)
    age = max(0.0, ts - seated)
    signal = (
        "registry_started_at"
        if registry_started_at(str(row.get("registration_id") or "") or None)
        is not None
        else "watch_seated_at"
    )
    handoff = assess_standing_handoff(thread_id, now=ts)
    if age < thr:
        return HopDecision(
            thread_id,
            "skip",
            "below_threshold",
            age_s=age,
            threshold_s=thr,
            signal=signal,
            handoff=handoff,
        )
    return HopDecision(
        thread_id,
        "fire",
        "age_threshold_met",
        age_s=age,
        threshold_s=thr,
        signal=signal,
        handoff=handoff,
    )


def mark_hop_fired(
    thread_id: str,
    *,
    now: float | None = None,
    path: Path | None = None,
    execution_id: str | None = None,
    satellite_execution_id: str | None = None,
    active_work_snap: dict[str, Any] | None = None,
    successor_birth_id: str | None = None,
    snapshot_reader: Callable[[], dict[str, Any]] | None = None,
) -> bool:
    """Reset seated_at after a cadence hop so the successor is not immediately re-hopped.

    Returns False when predecessor execution lookup fails (handle incomplete).
    On ``LOOKUP_FAILED`` with a live commission row in a fresh snapshot, advances
    ``registration_id`` via ``advance_registration_on_confirm`` before recording
    the failure — the hop still reports failure and takes cooldown.
    """
    from services.git_integration_worker.cursor_auto.hop_cadence_predecessor import (
        PredecessorConfirmError,
        capture_predecessor_at_hop,
        op_row_for_execution_on_lane,
        satellite_for_stargate_on_lane,
    )
    from services.git_integration_worker.cursor_auto.hop_cadence_stall_reconcile import (
        record_succession_claim,
    )
    from claude_bundles.hop_cadence_id_map import normalize_exclude_ids

    ts = time.time() if now is None else now
    watches = load_watches(path)
    row = dict(watches.get(thread_id) or {"thread_id": thread_id})
    row["thread_id"] = thread_id
    holder_reg = str(row.get("registration_id") or "").strip()
    sat_id = (satellite_execution_id or "").strip() or None
    if not sat_id and isinstance(active_work_snap, dict) and execution_id:
        sat_id = satellite_for_stargate_on_lane(
            active_work_snap,
            thread_id,
            stargate_execution_id=execution_id,
            holder_registration_id=holder_reg or None,
        )
    exclude_ids = normalize_exclude_ids(execution_id, sat_id)
    capture = capture_predecessor_at_hop(
        row,
        active_work_snap,
        exclude_execution_ids=exclude_ids,
    )
    if isinstance(capture, PredecessorConfirmError):
        if (
            capture.reason == "predecessor_execution_lookup_failed"
            and execution_id
        ):
            heal_snap: dict[str, Any] | None = None
            try:
                if snapshot_reader is not None:
                    heal_snap = snapshot_reader()
                else:
                    from services.git_integration_worker.cursor_auto.cdp_escalation import (
                        read_cdp_lane_snapshot,
                    )

                    heal_snap = read_cdp_lane_snapshot()
            except Exception as exc:  # noqa: BLE001 — heal must not crash fire path
                logger.warning(
                    "hop_cadence heal snapshot failed thread=%s: %s",
                    thread_id,
                    exc,
                )
            if isinstance(heal_snap, dict):
                aw_row = op_row_for_execution_on_lane(
                    heal_snap, thread_id, execution_id
                )
                if aw_row is not None:
                    dead_reg = str(row.get("registration_id") or "").strip()
                    updated, transition = advance_registration_on_confirm(
                        row,
                        matched_key=execution_id,
                        active_work_row=aw_row,
                        now=ts,
                        prior_registration_id=dead_reg,
                    )
                    if transition is not None:
                        watches[thread_id] = updated
                        save_watches(watches, path)
                        prior_reg, new_reg = transition
                        from services.git_integration_worker.cursor_auto.hop_cadence_events import (
                            emit_registration_advanced,
                        )

                        emit_registration_advanced(
                            thread_id=thread_id,
                            prior_registration_id=prior_reg,
                            new_registration_id=new_reg,
                            superseding_execution_id=execution_id,
                            superseded_execution_id=str(
                                row.get("superseded_execution_id") or ""
                            ),
                        )
        mark_hop_failed(
            thread_id,
            reason=capture.reason,
            now=ts,
            path=path,
        )
        return False
    if capture.verdict.value == "indeterminate":
        from services.git_integration_worker.cursor_auto.hop_cadence_events import (
            emit_binding_indeterminate,
        )

        emit_binding_indeterminate(
            thread_id=thread_id,
            reason=capture.absence_reason or "indeterminate",
        )
    pred_fields = capture.as_watch_fields()
    superseded = str(pred_fields.get("superseded_registration_id") or "").strip()
    # Never persist self-supersede (registration_id == superseded_registration_id).
    if holder_reg and superseded and holder_reg == superseded:
        pred_fields = {
            k: v
            for k, v in pred_fields.items()
            if k
            not in {
                "superseded_registration_id",
                "superseded_execution_id",
                "predecessor_verdict",
                "predecessor_absence_reason",
            }
        }
    row.update(pred_fields)
    row = record_succession_claim(
        row,
        execution_id=execution_id,
        satellite_execution_id=sat_id,
        now=ts,
    )
    row["last_hop_at"] = ts
    row["seated_at"] = ts
    row["enroll_source"] = "post_hop_reset"
    row.pop("consecutive_hop_failures", None)
    if execution_id:
        row["last_hop_execution_id"] = execution_id
        row["successor_execution_id"] = execution_id
    birth = (successor_birth_id or "").strip()
    if birth:
        row["successor_birth_id"] = birth
    watches[thread_id] = row
    save_watches(watches, path)
    superseded = str(row.get("superseded_registration_id") or "").strip()
    if superseded and row.get("pending_succession"):
        from claude_bundles.hop_cadence_lease_events import emit_fence_started

        pending = row.get("pending_succession")
        pending_dict = pending if isinstance(pending, dict) else {}
        emit_fence_started(
            thread_id=thread_id,
            superseded_registration_id=superseded,
            execution_id=execution_id,
            satellite_execution_id=sat_id
            or pending_dict.get("satellite_execution_id"),
        )
    return True


def persist_successor_birth_id(
    thread_id: str,
    successor_birth_id: str,
    *,
    path: Path | None = None,
) -> None:
    """Record the hop-body I6 key on the watch row for later stamp echo."""
    birth = (successor_birth_id or "").strip()
    if not birth or not thread_id.strip():
        return
    watches = load_watches(path)
    row = dict(watches.get(thread_id) or {"thread_id": thread_id})
    row["thread_id"] = thread_id
    row["successor_birth_id"] = birth
    watches[thread_id] = row
    save_watches(watches, path)


def advance_registration_on_confirm(
    row: dict[str, Any],
    *,
    matched_key: str,
    active_work_row: dict[str, Any] | None,
    now: float,
    prior_registration_id: str,
) -> tuple[dict[str, Any], tuple[str, str] | None]:
    """Advance watch registration when live membership confirms; emit events once per transition."""
    _ = now
    if active_work_row is None or not isinstance(active_work_row, dict):
        return row, None
    new_reg = str(active_work_row.get("registration_id") or "").strip()
    if not new_reg:
        return row, None
    prior_reg = prior_registration_id.strip()
    current_reg = str(row.get("registration_id") or "").strip()
    if current_reg == new_reg:
        return row, None
    updated = dict(row)
    updated["registration_id"] = new_reg
    # Heal self-supersede if prior capture poisoned the ledger.
    if str(updated.get("superseded_registration_id") or "").strip() == new_reg:
        from claude_bundles.hop_seat_cutover import clear_lease_fence_fields

        updated = clear_lease_fence_fields(updated)
    updated["succession_confirm_record"] = {
        "prior_registration_id": prior_reg,
        "superseded_execution_id": str(row.get("superseded_execution_id") or ""),
        "superseding_execution_id": matched_key,
        "confirmed_at": now,
    }
    return updated, (prior_reg, new_reg)


def mark_hop_failed(
    thread_id: str,
    *,
    reason: str,
    now: float | None = None,
    path: Path | None = None,
) -> None:
    """Apply the cooldown after a hop that produced no joinable execution id.

    A hop with no ``execution_id`` must not record a succession claim — the
    reconciler could never join a stall to it, so the revoke breaker would never
    count and the lane would burn CDP windows unchecked. But skipping
    ``mark_hop_fired`` entirely leaves ``last_hop_at`` unadvanced, so
    ``evaluate_watch`` re-fires on the very next scan: a ~30s hot loop in place
    of the 30m cadence, exactly when the substrate is already failing every
    generate. Advance the cooldown clock without claiming succession.
    """
    from services.git_integration_worker.cursor_auto.hop_cadence_stall_reconcile import (
        REVOKE_BREAKER_N,
    )

    ts = time.time() if now is None else now
    watches = load_watches(path)
    row = dict(watches.get(thread_id) or {"thread_id": thread_id})
    failures = int(row.get("consecutive_hop_failures") or 0) + 1
    row["thread_id"] = thread_id
    row["last_hop_at"] = ts
    row["last_hop_failure_at"] = ts
    row["last_hop_failure_reason"] = reason
    row["consecutive_hop_failures"] = failures
    # A lane that cannot produce a joinable id is failing just as surely as one
    # whose stall we can join; without this it would never reach the breaker,
    # because the breaker only counts revocations of claims that were joinable.
    if failures >= REVOKE_BREAKER_N:
        first_trip = not row.get("breaker_tripped")
        row["breaker_tripped"] = True
        row["breaker_tripped_at"] = ts
        row["breaker_trip_reason"] = reason
        if first_trip:
            # Same escalate surface as stall-revoke (AC4) — unjoinable hops
            # must be observable when they trip the breaker alone.
            from services.git_integration_worker.cursor_auto.hop_cadence_events import (
                emit_revoke_breaker,
            )

            emit_revoke_breaker(
                thread_id=thread_id,
                revocation_count=failures,
                breaker_n=REVOKE_BREAKER_N,
            )
    watches[thread_id] = row
    save_watches(watches, path)
