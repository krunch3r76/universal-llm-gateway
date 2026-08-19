"""Server-side caller identity resolution and lease gate at ``agent_bus.request``.

Census over ``identity_rows`` (execution-store ``rows`` ∪ registry
``seated_rows``). Bind order: caller wire → N≥2 refuse → origin CSR when
N≤1 → exactly-one operator-purpose match → unresolvable. Watch-row
``registration_id`` is lease SOT only — never promoted to admission identity.

N≠1 (``ambiguous_matches`` / ``zero_matches`` / ``empty_snap``) refuses at
enqueue. ``snap_load_failed`` and ``missing_thread_id`` still admit.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from universal_logging import get_logger

from claude_bundles import hop_seat_cutover
from claude_bundles.hop_cadence_lease_events import emit_identity_bound, emit_lease_lost
from claude_bundles.hop_cadence_seat_snap import attach_registry_seated_rows
from claude_bundles.hop_seat_cutover import resolve_request_refusal
from claude_bundles.request_admission_census import (
    REFUSE_CENSUS_REASONS,
    UnresolvableReason,
    census_match_ids,
    census_refusal_envelope,
    classify_unresolvable,
)

logger = get_logger(__name__)

GateOutcome = Literal["admit", "reject"]
IdentitySource = Literal[
    "caller_supplied",
    "origin_cse",
    "single_seat_active_work",
    "unresolvable",
]

_COUNTERS: dict[str, int] = defaultdict(int)


@dataclass(frozen=True)
class AdmissionIdentity:
    """Resolved seat identity for one request admission bind."""

    registration_id: str | None
    source: IdentitySource
    watch_present: bool
    unresolvable_reason: UnresolvableReason | None = None
    census_n: int = 0
    match_registration_ids: tuple[str, ...] = ()


def reset_identity_counters_for_tests() -> None:
    """Clear in-process counters (tests only)."""
    _COUNTERS.clear()


def get_identity_counters() -> dict[str, int]:
    """Return a snapshot of identity-on-gate counters."""
    return dict(_COUNTERS)


def _increment(name: str) -> None:
    _COUNTERS[name] += 1


def load_active_work_snap_result() -> tuple[dict[str, Any], bool]:
    """Return ``(snap, snap_load_failed)`` for admission bind.

    Fail-open: GET errors and non-dict payloads still return ``({}, True)`` so
    the gate admits. Failed loads increment ``active_work_snap_load_failed`` and
    log a warning so they are distinguishable from a successful empty snap.
    """
    try:
        from cdp_ask.client import CdpAskClient

        snap = CdpAskClient()._request("GET", "/v1/project-ask/active-work")
    except Exception as exc:
        _increment("active_work_snap_load_failed")
        logger.warning("active-work snap load failed err=%s", exc)
        return {}, True
    if not isinstance(snap, dict):
        _increment("active_work_snap_load_failed")
        logger.warning("active-work snap load non-dict type=%s", type(snap).__name__)
        return {}, True
    return snap, False


def load_active_work_snap() -> dict[str, Any]:
    """Best-effort active-work snapshot for admission bind (lib-owned)."""
    snap, _ = load_active_work_snap_result()
    return snap


def _resolve_origin_cse_registration(thread_id: str) -> str | None:
    """Registration from CSR projection for the lane thread, when present."""
    from claude_bundles.cdp_registry_store import load_sessions
    from claude_bundles.cse_session_common import find_session_by_thread

    sessions = load_sessions()
    found = find_session_by_thread(sessions, thread_id)
    if found is None:
        return None
    _, row = found
    ids = row.get("ids") or {}
    reg = str(ids.get("registration_id") or "").strip()
    return reg or None


def _emit_identity_gated(
    *,
    identity: AdmissionIdentity,
    thread_id: str,
    outcome: GateOutcome,
    reject_reason: str | None = None,
) -> None:
    """Emit ``mcp.agentbus.request.identity_gated`` on every gate return (best-effort)."""
    try:
        from mcp_events import record
    except ImportError:
        return
    record(
        "mcp.agentbus.request.identity_gated",
        role="coordination",
        identity_source=identity.source,
        unresolvable_reason=identity.unresolvable_reason,
        watch_present=identity.watch_present,
        registration_id=identity.registration_id,
        thread_id=thread_id or None,
        outcome=outcome,
        reject_reason=reject_reason,
        census_n=identity.census_n,
    )


def resolve_request_admission_identity(
    *,
    thread_id: str | None,
    caller_registration_id: str | None,
    active_work_snap: dict[str, Any] | None = None,
    path: Path | None = None,
    snap_load_failed: bool = False,
) -> AdmissionIdentity:
    """Resolve caller registration_id for admission bind (never from watch row)."""
    tid = (thread_id or "").strip()
    watch_present = bool(tid and hop_seat_cutover.load_watches(path).get(tid))
    caller = (caller_registration_id or "").strip()
    snap = active_work_snap if active_work_snap is not None else {}
    matches = census_match_ids(tid, snap) if tid else []
    census_n = len(matches)
    match_ids = tuple(matches)

    if caller:
        return AdmissionIdentity(
            registration_id=caller,
            source="caller_supplied",
            watch_present=watch_present,
            census_n=census_n,
            match_registration_ids=match_ids,
        )

    if not tid:
        return AdmissionIdentity(
            registration_id=None,
            source="unresolvable",
            watch_present=False,
            unresolvable_reason="missing_thread_id",
            census_n=0,
        )

    if census_n >= 2:
        return AdmissionIdentity(
            registration_id=None,
            source="unresolvable",
            watch_present=watch_present,
            unresolvable_reason="ambiguous_matches",
            census_n=census_n,
            match_registration_ids=match_ids,
        )

    origin = _resolve_origin_cse_registration(tid)
    if origin:
        return AdmissionIdentity(
            registration_id=origin,
            source="origin_cse",
            watch_present=watch_present,
            census_n=census_n,
            match_registration_ids=match_ids,
        )

    if census_n == 1:
        return AdmissionIdentity(
            registration_id=matches[0],
            source="single_seat_active_work",
            watch_present=watch_present,
            census_n=census_n,
            match_registration_ids=match_ids,
        )

    return AdmissionIdentity(
        registration_id=None,
        source="unresolvable",
        watch_present=watch_present,
        unresolvable_reason=classify_unresolvable(
            tid=tid,
            snap=snap,
            snap_load_failed=snap_load_failed,
            matches=matches,
        ),
        census_n=census_n,
        match_registration_ids=match_ids,
    )


def gate_request_admission(
    *,
    thread_id: str | None,
    caller_registration_id: str | None,
    active_work_snap: dict[str, Any] | None = None,
    path: Path | None = None,
) -> dict[str, Any] | None:
    """Return ``None`` to admit, or a ProtocolError envelope.

    Census N≠1 (``ambiguous_matches`` / ``zero_matches`` / ``empty_snap``)
    refuses at enqueue. ``snap_load_failed`` and ``missing_thread_id`` still
    admit. Live loads attach registry ``seated_rows``; injected test snaps
    are left unchanged.
    """
    tid = (thread_id or "").strip()
    if active_work_snap is not None:
        snap = active_work_snap
        snap_load_failed = False
    else:
        snap, snap_load_failed = load_active_work_snap_result()
        snap = attach_registry_seated_rows(snap)
    identity = resolve_request_admission_identity(
        thread_id=tid or None,
        caller_registration_id=caller_registration_id,
        active_work_snap=snap,
        path=path,
        snap_load_failed=snap_load_failed,
    )
    _increment(f"identity_source:{identity.source}")

    if identity.watch_present:
        _increment("watch_lane_requests")
        emit_identity_bound(
            thread_id=tid,
            identity_source=identity.source,
            watch_present=True,
            registration_id=identity.registration_id,
        )

    if (
        identity.source == "unresolvable"
        and identity.unresolvable_reason in REFUSE_CENSUS_REASONS
    ):
        _increment("census_refuse")
        _increment(f"census_refuse:{identity.unresolvable_reason}")
        _emit_identity_gated(
            identity=identity,
            thread_id=tid,
            outcome="reject",
            reject_reason=str(identity.unresolvable_reason),
        )
        return census_refusal_envelope(
            thread_id=tid,
            reason=str(identity.unresolvable_reason),
            census_n=identity.census_n,
            identity_source=identity.source,
            match_registration_ids=identity.match_registration_ids,
        )

    if identity.watch_present and identity.source == "unresolvable":
        _increment("unresolvable_on_watch_lane")
        _emit_identity_gated(identity=identity, thread_id=tid, outcome="admit")
        return None

    bound = (identity.registration_id or "").strip()
    if not bound:
        _emit_identity_gated(identity=identity, thread_id=tid, outcome="admit")
        return None

    refusal = resolve_request_refusal(
        thread_id=tid,
        cse_registration_id=bound,
        snap=snap,
        path=path,
        identity_source=identity.source,
    )
    if refusal is None:
        _increment("admit")
        _emit_identity_gated(identity=identity, thread_id=tid, outcome="admit")
        return None

    _increment("lease_lost")
    data = refusal.get("data") if isinstance(refusal.get("data"), dict) else {}
    emit_lease_lost(
        thread_id=tid,
        registration_id=bound,
        identity_source=identity.source,
        superseded_registration_id=str(data.get("superseded_registration_id") or ""),
        successor_execution_id=data.get("successor_execution_id"),
    )
    _emit_identity_gated(
        identity=identity,
        thread_id=tid,
        outcome="reject",
        reject_reason=str(data.get("reason") or "hop_seat_refusal"),
    )
    return refusal


def observe_identity_on_gate(
    *,
    thread_id: str | None,
    caller_registration_id: str | None,
    active_work_snap: dict[str, Any] | None,
    path: Path | None = None,
) -> AdmissionIdentity:
    """Backward-compatible observation wrapper — delegates to bind + counterfactual."""
    identity = resolve_request_admission_identity(
        thread_id=thread_id,
        caller_registration_id=caller_registration_id,
        active_work_snap=active_work_snap,
        path=path,
    )
    _increment(f"identity_source:{identity.source}")
    if not identity.watch_present:
        return identity

    _increment("watch_lane_requests")
    if identity.source == "unresolvable":
        _increment("unresolvable_on_watch_lane")

    counterfactual_reg = identity.registration_id
    would_refuse = False
    if counterfactual_reg and active_work_snap is not None:
        refusal = resolve_request_refusal(
            thread_id=thread_id,
            cse_registration_id=counterfactual_reg,
            snap=active_work_snap,
            path=path,
            identity_source=identity.source,
        )
        would_refuse = refusal is not None

    if would_refuse:
        _increment("counterfactual_would_refuse")
    else:
        _increment("counterfactual_would_admit")

    emit_identity_bound(
        thread_id=str(thread_id or ""),
        identity_source=identity.source,
        watch_present=True,
        registration_id=identity.registration_id,
    )
    return identity


__all__ = [
    "AdmissionIdentity",
    "gate_request_admission",
    "get_identity_counters",
    "load_active_work_snap",
    "load_active_work_snap_result",
    "observe_identity_on_gate",
    "reset_identity_counters_for_tests",
    "resolve_request_admission_identity",
]
