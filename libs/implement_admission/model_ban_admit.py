"""Shared admit-path helpers for model bans (override waiver + envelopes)."""

from __future__ import annotations

from typing import Any

from implement_admission.model_ban import (
    ban_override_fields,
    validate_ban_override_pair,
)


def ban_override_pair_reject(
    ban_override: Any,
    ban_override_reason: Any,
    *,
    status_code: int = 422,
) -> dict[str, Any] | None:
    code = validate_ban_override_pair(ban_override, ban_override_reason)
    if code is None:
        return None
    field = (
        "ban_override_reason"
        if code == "ban_override_reason_required"
        else "ban_override"
    )
    return {
        "admitted": False,
        "status_code": status_code,
        "code": code,
        "field": field,
        "reason": (
            "ban_override_reason is required when ban_override=true"
            if code == "ban_override_reason_required"
            else "ban_override=true is required when ban_override_reason is set"
        ),
        "details": ban_override_fields(),
    }


def collect_model_ban_overrides(admit_plan: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not admit_plan:
        return []
    raw = admit_plan.get("model_ban_overrides")
    out: list[dict[str, Any]] = []
    if isinstance(raw, list):
        out.extend(dict(x) for x in raw if isinstance(x, dict))
    legacy = admit_plan.get("model_ban_override")
    if isinstance(legacy, dict):
        out.append(dict(legacy))
    return out


def waiver_for_resolved_model(
    *,
    resolved_model: str,
    admit_plan: dict[str, Any] | None,
    body_ban_override: bool = False,
    body_ban_override_reason: str | None = None,
) -> tuple[bool, str | None, str | None]:
    """Return (enabled, reason, event_id) for a worker POST after team_dispatch admit."""
    enabled, reason = extract_model_ban_waiver(
        resolved_model=resolved_model,
        body_ban_override=body_ban_override,
        body_ban_override_reason=body_ban_override_reason,
        admit_plan=admit_plan,
    )
    if not enabled:
        return False, None, None
    event_id: str | None = None
    plan = admit_plan or {}
    events = plan.get("model_ban_override_events")
    if isinstance(events, list):
        for row in events:
            if not isinstance(row, dict):
                continue
            if str(row.get("model") or "").strip() == str(resolved_model).strip():
                eid = str(row.get("event_id") or "").strip()
                if eid:
                    event_id = eid
                break
    return True, reason, event_id


def extract_model_ban_waiver(
    *,
    resolved_model: str,
    body_ban_override: bool = False,
    body_ban_override_reason: str | None = None,
    admit_plan: dict[str, Any] | None = None,
) -> tuple[bool, str | None]:
    if body_ban_override is True:
        return True, str(body_ban_override_reason or "").strip() or None
    for row in collect_model_ban_overrides(admit_plan):
        if str(row.get("model") or "").strip() == str(resolved_model).strip():
            return True, str(row.get("reason") or "").strip() or None
    return False, None
