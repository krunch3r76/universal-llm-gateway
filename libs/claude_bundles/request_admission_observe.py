"""Backward-compatible identity observation wrapper (counterfactual metrics)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from claude_bundles.hop_cadence_lease_events import emit_identity_bound
from claude_bundles.hop_seat_cutover import resolve_request_refusal
from claude_bundles.request_admission_identity import (
    AdmissionIdentity,
    _increment,
    resolve_request_admission_identity,
)


def observe_identity_on_gate(
    *,
    thread_id: str | None,
    caller_registration_id: str | None,
    from_agent: str | None = None,
    active_work_snap: dict[str, Any] | None,
    path: Path | None = None,
) -> AdmissionIdentity:
    """Backward-compatible observation wrapper — delegates to bind + counterfactual."""
    identity = resolve_request_admission_identity(
        thread_id=thread_id,
        caller_registration_id=caller_registration_id,
        from_agent=from_agent,
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


__all__ = ["observe_identity_on_gate"]
