"""Census over ``identity_rows`` for request-admission identity.

N≠1 buckets ``ambiguous_matches`` / ``zero_matches`` / ``empty_snap`` refuse
at enqueue. ``snap_load_failed`` is classified only when the census is still
empty after attach — posting must not couple to cdp-ask availability.
"""

from __future__ import annotations

from typing import Any, Literal

from universal_protocol.errors import ProtocolError

from claude_bundles.cdp_registry.models import _HOST_LISTABLE_STATUSES
from claude_bundles.hop_cadence_seat_snap import (
    CURSOR_AUTO_CLAIMED_ROWS_KEY,
    identity_rows,
    is_live_stream_state,
)
from claude_bundles.what_is_running_view import OPERATOR_PURPOSES

_CLAIMED_SUPERSEDE_PHASES = frozenset(
    {"claimed_pre_admit", "admitted", "bound"},
)
_RELAY_PAST_NESTED = frozenset({"sdk_terminal", "closeout_posted"})

UnresolvableReason = Literal[
    "missing_thread_id",
    "snap_load_failed",
    "empty_snap",
    "zero_matches",
    "ambiguous_matches",
    "wire_id_not_in_census",
]

REFUSE_CENSUS_REASONS: frozenset[UnresolvableReason] = frozenset(
    {
        "ambiguous_matches",
        "zero_matches",
        "empty_snap",
        "wire_id_not_in_census",
    }
)

# ``zero_matches`` / ``empty_snap`` bind hop-seat lanes whose watch holds a
# registration. A registration-less enroll stub is not that lease — admit,
# same as an unwatched cursor-auto thread. Unwatched threads have no CSE
# census row and already admit rather than refuse continue.
_WATCHED_ONLY_REFUSE_REASONS: frozenset[UnresolvableReason] = frozenset(
    {"zero_matches", "empty_snap"}
)


def should_refuse_census(
    *,
    unresolvable_reason: UnresolvableReason | None,
    watch_present: bool,
    watch_has_holder: bool = False,
) -> bool:
    """Return whether enqueue should refuse on census miss.

    ``zero_matches`` / ``empty_snap`` refuse only when the watch row is
    present and holds a ``registration_id``. An enroll stub (a watch dict
    with no holder) does not refuse.
    """
    if unresolvable_reason not in REFUSE_CENSUS_REASONS:
        return False
    if unresolvable_reason in {"ambiguous_matches", "wire_id_not_in_census"}:
        return True
    if unresolvable_reason not in _WATCHED_ONLY_REFUSE_REASONS:
        return False
    return watch_present and watch_has_holder


def claimed_auto_job_counts_for_census(observer: dict[str, Any]) -> bool:
    """True when a persisted cursor-auto job row should count as census N=1.

    Claimed incumbents count in the supersede-candidate phases, before nested
    SDK finish. Queued incumbents count on status alone: their phase is
    ``queued``, not a claimed phase, and they have no process to cancel.
    Counting the row lets same-thread admission reach enqueue. ``queue_withdraw``
    versus ``run_cancel`` is decided later from job status, not from this
    predicate. ``done`` and ``failed`` stay excluded. A relay phase already
    past nested SDK finish stays excluded for both statuses.
    """
    relay = str(observer.get("relay_phase") or "none")
    if relay in _RELAY_PAST_NESTED:
        return False
    status = str(observer.get("status") or "")
    if status == "queued":
        return True
    if status != "claimed":
        return False
    phase = str(observer.get("lifecycle_phase") or "")
    return phase in _CLAIMED_SUPERSEDE_PHASES


def census_row_from_claimed_auto_job(
    *,
    thread_id: str,
    job_id: str,
    cse_registration_id: str | None = None,
    source: str = "cursor-auto-claimed",
) -> dict[str, Any]:
    """Synthetic identity row for a live Auto incumbent on ``thread_id``.

    ``stream_state=running`` is the census membership bit (``identity_rows``
    counts live stream state). It is not a claim that a process exists.
    Queued incumbents pass ``source=cursor-auto-queued``.
    """
    tid = (thread_id or "").strip()
    jid = (job_id or "").strip()
    reg = (cse_registration_id or "").strip() or f"cursor-auto-job:{jid}"
    return {
        "parent_thread": tid,
        "registration_id": reg,
        "session": "operator-proxy",
        "stream_state": "running",
        "status": "running",
        "source": source,
        "job_id": jid,
    }


def attach_claimed_job_census_rows(
    snap: dict[str, Any],
    rows: list[dict[str, Any]],
) -> dict[str, Any]:
    """Attach job-keyed census rows without mutating execution-store ``rows``."""
    if not rows:
        return snap
    out = dict(snap)
    existing = [
        row
        for row in (
            out.get(CURSOR_AUTO_CLAIMED_ROWS_KEY)
            if isinstance(out.get(CURSOR_AUTO_CLAIMED_ROWS_KEY), list)
            else []
        )
        if isinstance(row, dict)
    ]
    out[CURSOR_AUTO_CLAIMED_ROWS_KEY] = [*existing, *rows]
    return out


def _row_counts_for_census(row: dict[str, Any]) -> bool:
    """Census reads live ``stream_state`` or listable ``seat_state`` on the union."""
    if is_live_stream_state(str(row.get("stream_state") or "")):
        return True
    seat_state = str(row.get("seat_state") or "")
    return seat_state in _HOST_LISTABLE_STATUSES


def resolve_hop_successor_registration_id(
    snap: dict[str, Any],
    *,
    parent_thread: str,
    execution_id: str | None,
) -> str | None:
    """``register_lane`` mint for a hop's satellite execution on the lane.

    Successor retire/bind identity is the mint registration, not the bus
    ``successor_birth_id`` alone. Prefer the live OP row for *execution_id*;
    fall back to a registry seated row on the lane with the same execution.
    """
    lane = (parent_thread or "").strip()
    exec_id = (execution_id or "").strip()
    if not lane or not exec_id:
        return None
    for row in identity_rows(snap):
        if not is_live_stream_state(str(row.get("stream_state") or "")):
            continue
        purpose = str(row.get("purpose") or "").strip().lower()
        if purpose not in {"operator-proxy", "mission", "operator_proxy"}:
            continue
        if str(row.get("parent_thread") or "").strip() != lane:
            continue
        if str(row.get("execution_id") or "").strip() == exec_id:
            reg = str(row.get("registration_id") or "").strip()
            if reg:
                return reg
    for row in identity_rows(snap):
        if str(row.get("parent_thread") or "").strip() != lane:
            continue
        if str(row.get("purpose") or "") not in OPERATOR_PURPOSES:
            continue
        if str(row.get("execution_id") or "").strip() != exec_id:
            continue
        reg = str(row.get("registration_id") or "").strip()
        if reg:
            return reg
    return None


def census_match_ids(thread_id: str, snap: dict[str, Any]) -> list[str]:
    """Unique operator-purpose registration ids on ``thread_id`` from the union."""
    retired = {
        str(item).strip()
        for item in (snap.get("retired_registration_ids") or [])
        if str(item).strip()
    }
    matches: list[str] = []
    seen: set[str] = set()
    for row in identity_rows(snap):
        if not _row_counts_for_census(row):
            continue
        if str(row.get("purpose") or "") not in OPERATOR_PURPOSES:
            continue
        if str(row.get("parent_thread") or "").strip() != thread_id:
            continue
        reg = str(row.get("registration_id") or "").strip()
        if not reg or reg in seen or reg in retired:
            continue
        seen.add(reg)
        matches.append(reg)
    return matches


def classify_unresolvable(
    *,
    tid: str,
    snap: dict[str, Any],
    snap_load_failed: bool,
    matches: list[str],
) -> UnresolvableReason:
    """Bucket an N≠1 (or blind) miss. ``matches`` already computed by the caller."""
    if not tid:
        return "missing_thread_id"
    if matches:
        return "ambiguous_matches"
    if snap_load_failed:
        return "snap_load_failed"
    if not identity_rows(snap):
        return "empty_snap"
    return "zero_matches"


def census_refusal_envelope(
    *,
    thread_id: str,
    reason: str,
    census_n: int,
    identity_source: str,
    match_registration_ids: tuple[str, ...],
) -> dict[str, Any]:
    """Structured enqueue refusal the caller can read — no queue-behind."""
    return ProtocolError(
        code="seat.identity_unresolvable",
        message=(
            "request: admission identity census "
            f"N={census_n} reason={reason}; "
            "refusing enqueue rather than queueing behind"
        ),
        source="rpc",
        retryable=False,
        data={
            "thread_id": thread_id or None,
            "reason": reason,
            "census_n": census_n,
            "identity_source": identity_source,
            "match_registration_ids": list(match_registration_ids),
        },
    ).to_dict()


def wire_id_not_in_census_envelope(
    *,
    thread_id: str,
    census_n: int,
    identity_source: str,
    match_registration_ids: tuple[str, ...],
    wire_registration_id: str,
) -> dict[str, Any]:
    """Refuse a caller wire id that is outside a non-empty census set."""
    return ProtocolError(
        code="seat.wire_id_not_in_census",
        message=(
            "request: wire cse_registration_id is not in the lane census "
            f"N={census_n}; refusing enqueue rather than binding the stale id"
        ),
        source="rpc",
        retryable=False,
        data={
            "thread_id": thread_id or None,
            "reason": "wire_id_not_in_census",
            "census_n": census_n,
            "identity_source": identity_source,
            "match_registration_ids": list(match_registration_ids),
            "wire_registration_id": wire_registration_id,
        },
    ).to_dict()
