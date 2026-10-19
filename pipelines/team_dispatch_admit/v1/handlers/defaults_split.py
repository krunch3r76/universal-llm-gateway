"""Resolution helpers for team_dispatch_admit defaults (split for SLOC cap)."""

from __future__ import annotations

from typing import Any

from agent_seat.dispatch_role_catalog import auto_seats, generate_roles
from agent_seat.profiles import CapabilityProfile, get_profile, seat_to_family
from agent_seat.registry import normalize_agent_slug, normalize_bus_address
from cursor_capabilities import is_cursor_model_denied
from implement_admission.routing import load_route_policy
from implement_admission.workflow_registry import (
    MECHANICAL_WORKFLOW,
    load_auto_judgment_default_model,
    load_workflow_registry,
)

from ._envelope import Backend, is_cdp_model, is_cursor_substrate_model, reject_envelope


def _teach(axis: str) -> dict[str, Any]:
    return {
        "expected_axis": axis,
        "valid_seats": auto_seats(),
        "valid_roles": list(generate_roles()),
    }


def _resolve_auto_seat_profile(
    seat: str,
) -> tuple[str, str, str, CapabilityProfile]:
    canonical = normalize_agent_slug(seat)
    if canonical not in auto_seats():
        raise ValueError("seat_unknown")
    if canonical == "cursor-sdk":
        return "cursor-sdk", "cursor", "sdk", get_profile("cursor", "sdk")
    family = seat_to_family(canonical)
    parts = canonical.split("-", 1)
    if family is None or len(parts) != 2:
        raise ValueError("seat_unknown")
    platform = parts[1]
    profile = get_profile(family, platform)
    if not profile.auto_dispatchable:
        raise ValueError("seat_not_auto_dispatchable")
    return normalize_bus_address(f"{family}-{platform}"), family, platform, profile


def _is_sdk_substrate(profile: CapabilityProfile) -> bool:
    return (
        profile.family == "cursor"
        and profile.platform == "sdk"
        and profile.auto_dispatchable
    )


def _sdk_admission(
    *,
    role: str | None,
    seat: str | None,
    model: str | None,
) -> bool:
    if role is not None and normalize_agent_slug(role) == "cursor-sdk":
        return False
    if seat is not None and str(seat).strip():
        try:
            _resolve_auto_seat_profile(str(seat))
        except ValueError:
            return False
        return True
    return is_cursor_substrate_model(model)


def _select_backend(
    *,
    role: str | None,
    seat: str | None,
    model: str | None,
) -> Backend:
    if is_cdp_model(model):
        return "cdp"
    if role is not None and normalize_agent_slug(role) == "cursor-sdk":
        pass
    elif seat is not None and str(seat).strip() and _sdk_admission(
        role=None, seat=seat, model=None
    ):
        return "cursor_sdk"
    elif is_cursor_substrate_model(model):
        return "cursor_sdk"
    elif role is not None and str(role).strip():
        return "api_role"
    return "frontier"


def _omit_model(*, job: str | None) -> tuple[str, str]:
    registry = load_workflow_registry()
    key = (job or "").strip().lower()
    if key in {"implement", "mechanical"}:
        binding = registry.workflows.get(MECHANICAL_WORKFLOW)
        if binding is None or not binding.model:
            raise ValueError("sdk_generate_model_invalid")
        return binding.model, MECHANICAL_WORKFLOW
    wf = registry.workflow_for_contract(key) if key else None
    if wf is not None:
        return wf.model, wf.slug
    policy = load_route_policy()
    return load_auto_judgment_default_model(policy), "auto_judgment"


def _resolve_sdk_target(
    seat: str,
    *,
    model: str | None,
    job: str | None,
    workflow_override: str | None = None,
) -> tuple[str, str, str, str, str]:
    to_agent, family, platform, profile = _resolve_auto_seat_profile(seat)
    if profile.manual_handoff:
        raise ValueError("seat_not_auto_dispatchable")
    if not _is_sdk_substrate(profile):
        raise ValueError("sdk_substrate_required")
    resolved = model or _omit_model(job=job)[0]
    if resolved == "auto":
        resolved = "cursor/auto"
    if not resolved:
        raise ValueError("sdk_generate_model_invalid")
    if not resolved.startswith("cursor/"):
        raise ValueError("sdk_generate_model_invalid")
    if profile.family == "cursor" and profile.platform == "sdk":
        if is_cursor_model_denied(resolved):
            raise ValueError("sdk_generate_model_invalid")
    elif profile.allowed_models and resolved not in profile.allowed_models:
        raise ValueError("sdk_generate_model_invalid")
    if workflow_override:
        workflow = workflow_override
    elif not model:
        workflow = _omit_model(job=job)[1]
    else:
        workflow = _omit_model(job=job)[1]
    return to_agent, family, platform, resolved, workflow


def _seat_field_error(exc: ValueError) -> dict[str, Any]:
    code = str(exc)
    if code == "seat_unknown":
        return reject_envelope(
            status_code=422,
            code="seat_unknown",
            field="seat",
            reason="Unknown auto-dispatch seat",
            details=_teach("seat"),
        )
    if code == "seat_not_auto_dispatchable":
        return reject_envelope(
            status_code=422,
            code="seat_not_auto_dispatchable",
            field="seat",
            reason="seat is not auto-dispatchable",
            details=_teach("seat"),
        )
    if code == "sdk_substrate_required":
        return reject_envelope(
            status_code=422,
            code="sdk_substrate_required",
            field="seat",
            reason="SDK substrate required",
        )
    return reject_envelope(
        status_code=422,
        code="sdk_generate_model_invalid",
        field="model",
        reason="invalid sdk model",
    )
