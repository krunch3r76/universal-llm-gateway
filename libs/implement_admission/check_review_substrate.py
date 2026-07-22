"""Check/review dual-substrate resolution and admission matrix (dispatch-dual-substrate-option)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from model_id import ModelId

CHECK_REVIEW_DEFAULT_MODEL = "openai/gpt-5.6-terra"
CHECK_REVIEW_POLICY_KEY = "check_review_default_model"
CHECK_REVIEW_DECISION_CITATION = "decision:autonomous-work-item-spine"
CHECK_REVIEW_ASSERTION_CITATION = "assertion:23920"

CHECK_REVIEW_API_ROLES = frozenset({"reviewer", "skeptic"})
CURSOR_CHECK_REVIEW_MODELS = frozenset(
    {
        "cursor/gpt-5.6-terra",
        "cursor/gpt-5.6-sol",
        "cursor/gpt-5.6-luna",
        "cursor/grok-4.5",
    }
)
MECHANICAL_CURSOR_MODELS = frozenset(
    {"cursor/gemini-3.5-flash", "cursor/gemini-3.6-flash"}
)
SYNTHESIZER_ROLE = "synthesizer"
_MECHANICAL_PROFILE = "mechanical"
_JUDGMENT_ROLES = frozenset({"reviewer", "skeptic"})

_DECISION_CITATION_RE = re.compile(r"decision:[a-z0-9][-a-z0-9_]*", re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class CheckReviewResolution:
    resolved_model: str
    substrate: str
    delivery_from_role: str | None = None


def load_check_review_default_model(policy: dict[str, Any]) -> str:
    value = policy.get(CHECK_REVIEW_POLICY_KEY)
    if not isinstance(value, str) or not value.strip():
        return CHECK_REVIEW_DEFAULT_MODEL
    return value.strip()


def verify_check_review_default_conformance(
    policy: dict[str, Any],
    *,
    policy_text: str | None = None,
) -> list[str]:
    """Return errors when default key drifts without covering decision citation."""
    errors: list[str] = []
    if CHECK_REVIEW_POLICY_KEY not in policy:
        errors.append(f"missing required policy key {CHECK_REVIEW_POLICY_KEY!r}")
        return errors
    live = load_check_review_default_model(policy)
    if live != CHECK_REVIEW_DEFAULT_MODEL:
        haystack = policy_text or ""
        if not _DECISION_CITATION_RE.search(haystack):
            errors.append(
                f"{CHECK_REVIEW_POLICY_KEY}={live!r} differs from bound "
                f"{CHECK_REVIEW_DEFAULT_MODEL!r} without a covering decision:* citation"
            )
    return errors


def is_check_review_api_role(role: str) -> bool:
    return role.strip().lower() in CHECK_REVIEW_API_ROLES


def cursor_delivery_from_role(model: str) -> str | None:
    """Map cursor check/review model to gate-readable bus author role."""
    bare = ModelId.parse(model).api_model_id.lower()
    if bare.startswith("gpt-5.6") or bare == "gpt-5.5":
        return "reviewer"
    if bare.startswith("grok-4.5"):
        return "skeptic"
    return None


def is_cursor_check_review_model(model: str) -> bool:
    return model.strip().lower() in CURSOR_CHECK_REVIEW_MODELS


def resolve_check_review_model(
    role: str,
    model: str | None,
    *,
    policy: dict[str, Any] | None = None,
) -> CheckReviewResolution:
    """Resolve effective model + substrate for check/review-class dispatches."""
    from implement_admission.routing import load_route_policy

    loaded = policy or load_route_policy()
    default_model = load_check_review_default_model(loaded)
    canonical_role = role.strip().lower()

    if model:
        resolved = model.strip()
    elif canonical_role in CHECK_REVIEW_API_ROLES:
        if canonical_role == "skeptic":
            from agent_seat.registry import resolve_agent_model

            resolved = resolve_agent_model(role)
        else:
            resolved = default_model
    else:
        resolved = model or ""

    if not resolved:
        return CheckReviewResolution(
            resolved_model=default_model,
            substrate="api",
            delivery_from_role=canonical_role if canonical_role in _JUDGMENT_ROLES else None,
        )

    backend = ModelId.parse(resolved).backend_type
    if backend == "cursor_sdk":
        delivery = cursor_delivery_from_role(resolved)
        return CheckReviewResolution(
            resolved_model=resolved,
            substrate="cursor-sdk",
            delivery_from_role=delivery,
        )
    return CheckReviewResolution(
        resolved_model=resolved,
        substrate="api",
        delivery_from_role=canonical_role if canonical_role in _JUDGMENT_ROLES else None,
    )


@dataclass(frozen=True, slots=True)
class CheckReviewAdmissionReject:
    field: str
    reason: str
    code: str


def evaluate_check_review_admission(
    role: str,
    model: str | None,
    *,
    api_role_with_cursor_on_api_profile: bool,
) -> CheckReviewAdmissionReject | CheckReviewResolution | None:
    """Pure admission matrix; caller raises FrontierEndpointError when reject."""
    canonical = role.strip().lower()

    if (
        canonical == SYNTHESIZER_ROLE
        and model
        and ModelId.parse(model).backend_type == "cursor_sdk"
    ):
        return CheckReviewAdmissionReject(
            field="model",
            reason="synthesizer does not admit cursor-sdk substrate",
            code="substrate_unsupported_for_role",
        )

    if model and model.strip().lower() in MECHANICAL_CURSOR_MODELS:
        if canonical in _JUDGMENT_ROLES:
            return CheckReviewAdmissionReject(
                field="model",
                reason=(
                    f"{model!r} is mechanical-profile only and cannot serve "
                    f"judgment role {role!r}"
                ),
                code="profile_mismatch",
            )

    if canonical in CHECK_REVIEW_API_ROLES and model:
        if (
            ModelId.parse(model).backend_type == "cursor_sdk"
            and api_role_with_cursor_on_api_profile
        ):
            return CheckReviewAdmissionReject(
                field="model",
                reason=(
                    f"API role {role!r} with cursor model {model!r} requires "
                    "cursor-sdk admission path, not API substrate"
                ),
                code="sdk_substrate_required",
            )

    if canonical == "cursor-sdk" and model:
        from cursor_capabilities import (
            CURSOR_MODEL_CAPABILITIES,
            canonical_cursor_bare_id,
        )

        bare = canonical_cursor_bare_id(model)
        cap = CURSOR_MODEL_CAPABILITIES.get(bare)
        delivery = cursor_delivery_from_role(model)
        if delivery and cap and cap.instruction_profile == _MECHANICAL_PROFILE:
            return CheckReviewAdmissionReject(
                field="model",
                reason=f"model {model!r} profile_mismatch for check/review delivery",
                code="profile_mismatch",
            )

    if canonical in CHECK_REVIEW_API_ROLES or (
        canonical == "cursor-sdk" and model and is_cursor_check_review_model(model)
    ):
        return resolve_check_review_model(role, model)
    return None
