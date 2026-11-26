"""H1–H2 holder join and H3 probe application for :8 UUID endpoint subscriptions."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from typing import Any

from bus_watch.endpoint_subscription_registry import (
    RegistryClaims,
    claim_tab_uuids,
    live_claims_for_scope,
    scope_claim_stale,
)
from bus_watch.endpoint_subscription_row_parse import (
    enumerate_display8_uuid_rows,
    h1_rows_for_scope,
    is_uuid_agent_id,
    parse_tab_uuid,
)

__all__ = [
    "HolderResolution",
    "RegistryClaims",
    "apply_holder_h3",
    "claim_tab_uuids",
    "enumerate_display8_uuid_rows",
    "fetch_registry_claims",
    "h1_rows_for_scope",
    "is_uuid_agent_id",
    "live_claims_for_scope",
    "parse_tab_uuid",
    "per_row_actuation_refused",
    "resolve_holder",
    "resolve_holder_h12",
    "scope_claim_stale",
]

# Re-export registry fetch at join facade boundary for monitor imports.
from bus_watch.endpoint_subscription_registry import fetch_registry_claims  # noqa: E402


@dataclass(frozen=True)
class HolderResolution:
    holder_uuid: str | None
    holder_row: dict[str, Any] | None
    actuation_refused: str | None
    scope_eligible: bool
    actuation_eligible: bool


def resolve_holder_h12(
    scope: str,
    h1_rows: list[dict[str, Any]],
    registry: RegistryClaims,
    *,
    now: datetime | None = None,
) -> HolderResolution:
    """H1∧H2 join only — H3 applied via ``apply_holder_h3``."""
    if registry.refusal == "claim_source_unavailable":
        return HolderResolution(
            None, None, "claim_source_unavailable", False, False
        )
    if not h1_rows:
        return HolderResolution(None, None, "unknown", False, False)
    if registry.refusal == "claim_source_truncated":
        return HolderResolution(
            None, None, "claim_source_truncated", False, False
        )

    claims = live_claims_for_scope(registry, scope, now=now)
    claim_uuids = claim_tab_uuids(claims)

    if scope_claim_stale(claim_uuids, h1_rows):
        return HolderResolution(None, None, "stale", False, False)

    h12: list[dict[str, Any]] = []
    for row in h1_rows:
        uid = is_uuid_agent_id(row.get("agent_id"))
        if uid and uid in claim_uuids:
            h12.append(row)

    distinct = {
        is_uuid_agent_id(row.get("agent_id"))
        for row in h12
        if is_uuid_agent_id(row.get("agent_id"))
    }
    if len(distinct) >= 2:
        return HolderResolution(None, None, "ambiguous", False, False)

    if not h12:
        return HolderResolution(None, None, "no_live_claim", False, False)

    holder_row = h12[0]
    holder_uuid = is_uuid_agent_id(holder_row.get("agent_id"))
    assert holder_uuid is not None
    return HolderResolution(holder_uuid, holder_row, None, True, False)


def apply_holder_h3(
    resolution: HolderResolution,
    budget_probe: str | None,
) -> HolderResolution:
    """Apply H3 (glass.cdp probe) for the resolved holder U only."""
    if resolution.holder_uuid is None or resolution.actuation_refused:
        return resolution
    probe = budget_probe
    if probe is None or (isinstance(probe, str) and not str(probe).strip()):
        return replace(
            resolution,
            actuation_refused="incomplete",
            actuation_eligible=False,
        )
    if probe == "incomplete":
        return replace(
            resolution,
            actuation_refused="incomplete",
            actuation_eligible=False,
        )
    if probe != "glass.cdp":
        return replace(
            resolution,
            actuation_refused="probe_not_glass_cdp",
            actuation_eligible=False,
        )
    return replace(resolution, actuation_refused=None, actuation_eligible=True)


_MISSING_H3: object = object()


def resolve_holder(
    scope: str,
    h1_rows: list[dict[str, Any]],
    registry: RegistryClaims,
    *,
    budget_probe: str | None | object = _MISSING_H3,
    now: datetime | None = None,
) -> HolderResolution:
    """Full H1∧H2∧H3 when ``budget_probe`` is passed; H12-only when omitted."""
    base = resolve_holder_h12(scope, h1_rows, registry, now=now)
    if budget_probe is _MISSING_H3:
        return base
    return apply_holder_h3(base, budget_probe)  # type: ignore[arg-type]


def per_row_actuation_refused(
    row: dict[str, Any],
    scope_resolution: HolderResolution,
    *,
    claim_uuids: set[str],
    h1_rows: list[dict[str, Any]],
) -> str | None:
    if scope_resolution.actuation_refused in {
        "claim_source_unavailable",
        "claim_source_truncated",
        "unknown",
        "ambiguous",
    }:
        return scope_resolution.actuation_refused
    uid = is_uuid_agent_id(row.get("agent_id"))
    if uid and uid not in claim_uuids and claim_uuids:
        row_uuids = {is_uuid_agent_id(r.get("agent_id")) for r in h1_rows}
        if uid not in row_uuids:
            return "stale"
    row_id = int(row.get("id") or 0)
    holder_row = scope_resolution.holder_row
    if holder_row is not None and uid:
        holder_id = int(holder_row.get("id") or 0)
        if uid in claim_uuids and row_id < holder_id:
            return "stale"
    if scope_resolution.actuation_refused:
        if (
            scope_resolution.holder_uuid
            and uid == scope_resolution.holder_uuid
        ):
            return scope_resolution.actuation_refused
        return scope_resolution.actuation_refused
    if scope_resolution.holder_uuid and uid != scope_resolution.holder_uuid:
        return "no_live_claim"
    return scope_resolution.actuation_refused