"""Census over ``identity_rows`` for request-admission identity.

N≠1 buckets ``ambiguous_matches`` / ``zero_matches`` / ``empty_snap`` refuse
at enqueue. ``snap_load_failed`` is classified only when the census is still
empty after attach — posting must not couple to cdp-ask availability.
"""

from __future__ import annotations

from typing import Any, Literal

from universal_protocol.errors import ProtocolError

from claude_bundles.hop_cadence_seat_snap import identity_rows
from claude_bundles.what_is_running_view import OPERATOR_PURPOSES

UnresolvableReason = Literal[
    "missing_thread_id",
    "snap_load_failed",
    "empty_snap",
    "zero_matches",
    "ambiguous_matches",
]

_ACTIVE_STATUSES = frozenset({"pending", "running"})
REFUSE_CENSUS_REASONS: frozenset[UnresolvableReason] = frozenset(
    {"ambiguous_matches", "zero_matches", "empty_snap"}
)


def census_match_ids(thread_id: str, snap: dict[str, Any]) -> list[str]:
    """Unique operator-purpose registration ids on ``thread_id`` from the union."""
    matches: list[str] = []
    seen: set[str] = set()
    for row in identity_rows(snap):
        if str(row.get("status") or "") not in _ACTIVE_STATUSES:
            continue
        if str(row.get("purpose") or "") not in OPERATOR_PURPOSES:
            continue
        if str(row.get("parent_thread") or "").strip() != thread_id:
            continue
        reg = str(row.get("registration_id") or "").strip()
        if reg and reg not in seen:
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
