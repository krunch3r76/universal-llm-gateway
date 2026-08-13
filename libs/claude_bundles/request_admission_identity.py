"""Server-side caller identity resolution at ``agent_bus.request`` admission.

Resolves ``registration_id`` from the hop watch row when the caller omits
``cse_registration_id``. Observation-only in this slice — admission refusal
still keys off caller-supplied identity only (measure-before-you-fence).
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from claude_bundles import hop_seat_cutover
from claude_bundles.hop_seat_cutover import resolve_request_refusal

IdentitySource = Literal[
    "caller_supplied",
    "watch_row",
    "unresolvable",
]

_COUNTERS: dict[str, int] = defaultdict(int)


@dataclass(frozen=True)
class AdmissionIdentity:
    """Resolved seat identity for one request admission observation."""

    registration_id: str | None
    source: IdentitySource
    watch_present: bool


def reset_identity_counters_for_tests() -> None:
    """Clear in-process counters (tests only)."""
    _COUNTERS.clear()


def get_identity_counters() -> dict[str, int]:
    """Return a snapshot of identity-on-gate counters."""
    return dict(_COUNTERS)


def _increment(name: str) -> None:
    _COUNTERS[name] += 1


def resolve_request_admission_identity(
    *,
    thread_id: str | None,
    caller_registration_id: str | None,
    path: Path | None = None,
) -> AdmissionIdentity:
    """Resolve caller registration_id without altering admission inputs."""
    caller = (caller_registration_id or "").strip()
    if caller:
        tid = (thread_id or "").strip()
        watch_present = bool(tid and hop_seat_cutover.load_watches(path).get(tid))
        return AdmissionIdentity(
            registration_id=caller,
            source="caller_supplied",
            watch_present=watch_present,
        )

    tid = (thread_id or "").strip()
    if not tid:
        return AdmissionIdentity(
            registration_id=None,
            source="unresolvable",
            watch_present=False,
        )

    row = hop_seat_cutover.load_watches(path).get(tid)
    if not row:
        return AdmissionIdentity(
            registration_id=None,
            source="unresolvable",
            watch_present=False,
        )

    reg = str(row.get("registration_id") or "").strip()
    if reg:
        return AdmissionIdentity(
            registration_id=reg,
            source="watch_row",
            watch_present=True,
        )
    return AdmissionIdentity(
        registration_id=None,
        source="unresolvable",
        watch_present=True,
    )


def observe_identity_on_gate(
    *,
    thread_id: str | None,
    caller_registration_id: str | None,
    active_work_snap: dict[str, Any] | None,
    path: Path | None = None,
) -> AdmissionIdentity:
    """Record identity source + counterfactual refusal for watch-bearing lanes."""
    identity = resolve_request_admission_identity(
        thread_id=thread_id,
        caller_registration_id=caller_registration_id,
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
        )
        would_refuse = refusal is not None

    if would_refuse:
        _increment("counterfactual_would_refuse")
    else:
        _increment("counterfactual_would_admit")

    try:
        from mcp_events import record

        record(
            "mcp.agentbus.request.identity_observed",
            thread=thread_id,
            identity_source=identity.source,
            watch_present=identity.watch_present,
            registration_id=identity.registration_id,
            counterfactual_would_refuse=would_refuse,
        )
    except Exception:
        pass

    return identity


__all__ = [
    "AdmissionIdentity",
    "get_identity_counters",
    "observe_identity_on_gate",
    "reset_identity_counters_for_tests",
    "resolve_request_admission_identity",
]
