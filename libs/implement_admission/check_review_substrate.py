"""Check/review dual-substrate admission plus consultant-identity independence.

``ConsultantIdentity`` / ``independently_measured`` are the (model, effort-rung)
independence key: two seats are independent when their folded model identities
differ, or when they are the same model at different effort rungs; unknown
identity or unmeasured rung never certifies.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from claude_bundles.chat_model_match import (
    normalize_picker_request,
    parse_model_request,
    sealed_ask_default_effort,
)
from cursor_capabilities import (
    CURSOR_MODEL_CAPABILITIES,
    default_variant,
    effective_knobs,
    effort_knob_name,
)
from effort_vocabulary import normalize_effort
from model_id import ModelId

# Code-lane standing default (decision:code-review-panel-cursor-substrate).
# Explicit model=openai/* still admits on role=reviewer API path.
CHECK_REVIEW_DEFAULT_MODEL = "cursor/gpt-5.6-terra"
CHECK_REVIEW_POLICY_KEY = "check_review_default_model"
CHECK_REVIEW_DECISION_CITATION = "decision:code-review-panel-cursor-substrate"
CHECK_REVIEW_ASSERTION_CITATION = "assertion:26392"

CHECK_REVIEW_API_ROLES = frozenset({"reviewer", "skeptic"})
CURSOR_CHECK_REVIEW_MODELS = frozenset(
    {
        "cursor/gpt-5.6-terra",
        "cursor/gpt-5.6-sol",
        "cursor/gpt-5.6-luna",
        "cursor/grok-4.6",
    }
)
MECHANICAL_CURSOR_MODELS = frozenset(
    {"cursor/gemini-3.5-flash", "cursor/gemini-3.6-flash"}
)
SYNTHESIZER_ROLE = "synthesizer"
_MECHANICAL_PROFILE = "mechanical"
_JUDGMENT_ROLES = frozenset({"reviewer", "skeptic"})

_DECISION_CITATION_RE = re.compile(r"decision:[a-z0-9][-a-z0-9_]*", re.IGNORECASE)

# Identity token when no catalogued model matches. Must never equal a
# substrate slug or a real bare id — a later ``!=`` must not certify it.
UNKNOWN_MODEL_IDENTITY = "unknown"

# Vendor-spelling fold: CDP picker slugs → cursor/API bare wire id (the
# ``CURSOR_MODEL_CAPABILITIES`` key). One locus; ``normalize_picker_request``
# owns the floating ``fable`` alias, this table owns spelling only.
_IDENTITY_ALIASES: dict[str, str] = {
    "fable-5.1": "claude-fable-5-1",
    "fable-5": "claude-fable-5",
    "opus-5": "claude-opus-5",
    "sonnet-5": "claude-sonnet-5",
    "haiku-4.5": "claude-haiku-4-5",
}

# Knob names that carry a reasoning-effort rung, in precedence order.
_EFFORT_KNOB_KEYS: tuple[str, ...] = ("effort", "reasoning", "reasoning_effort")


@dataclass(frozen=True, slots=True)
class ConsultantIdentity:
    """Independence key for one consultant seat: ``(model_identity, rung)``.

    ``model_identity`` is the substrate-free, effort-free, alias-folded bare
    model id (``claude-fable-5-1``), or ``"unknown"``. ``rung`` is a
    ``normalize_effort`` token, or ``None`` when the seat exposes no effort
    knob — a pair with a ``None`` rung on the same model is never independent.
    """

    model_identity: str
    rung: str | None


@dataclass(frozen=True, slots=True)
class CheckReviewResolution:
    resolved_model: str
    substrate: str
    delivery_from_role: str | None = None


def load_check_review_default_model(policy: dict[str, Any]) -> str:
    """Return the configured check/review default model, or the standing cursor pin."""
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
    """True when ``role`` is a check/review API role (reviewer or skeptic)."""
    return role.strip().lower() in CHECK_REVIEW_API_ROLES


def coerce_check_review_omit_to_cursor_seat(
    role: str | None,
    seat: str | None,
    model: str | None,
    *,
    policy: dict[str, Any] | None = None,
) -> tuple[str | None, str | None, str | None, bool]:
    """When check/review role omits model= and default is cursor/, coerce to seat=.

    ``role=reviewer`` + omit model must not land on the API path with a cursor
    default (substrate_model_role_conflict / broken API transport). Returns
    ``(role, seat, model, coerced)``.
    """
    if seat is not None and str(seat).strip():
        return role, seat, model, False
    if model is not None:
        return role, seat, model, False
    if role is None or not is_check_review_api_role(role):
        return role, seat, model, False
    resolution = resolve_check_review_model(role, None, policy=policy)
    if resolution.substrate != "cursor-sdk":
        return role, seat, model, False
    return None, "cursor-sdk", resolution.resolved_model, True


def _bare_and_backend(model: str) -> tuple[str, str | None]:
    """Return ``(bare_wire_id, backend_type)`` or ``("", None)`` on failure."""
    raw = (model or "").strip()
    if not raw:
        return "", None
    try:
        parsed = ModelId.parse(raw)
    except ValueError:
        return "", None
    bare = parsed.api_model_id.lower()
    backend_type = parsed.backend_type
    if backend_type == "cdp":
        bare = normalize_picker_request(raw).lower()
    if "__effort_" in bare:
        bare = bare.partition("__effort_")[0]
    return bare, backend_type


def model_identity(model: str) -> str:
    """Fold a routed model id to its catalogued bare wire identity.

    Strips substrate prefixes, CDP picker aliases, cloud-proxy effort suffixes,
    and trailing effort tokens, then maps vendor spelling via ``_IDENTITY_ALIASES``.
    Returns ``UNKNOWN_MODEL_IDENTITY`` when parsing fails or the id is absent from
    ``CURSOR_MODEL_CAPABILITIES`` — prefix heuristics are intentionally refused.
    """
    bare, _backend = _bare_and_backend(model)
    if not bare:
        return UNKNOWN_MODEL_IDENTITY
    family, _suffix_effort = parse_model_request(bare)
    folded = _IDENTITY_ALIASES.get(family, family)
    if folded in CURSOR_MODEL_CAPABILITIES:
        return folded
    return UNKNOWN_MODEL_IDENTITY


def consultant_rung(model: str, knobs: Mapping[str, str] | None = None) -> str | None:
    """Return the normalized effort rung for one consultant seat, or ``None``.

    Explicit effort-like knobs win over id suffixes and substrate defaults.
    Cloud API paths and models without an effort knob fail closed (``None``).
    Unknown catalog identity always yields ``None``.
    """
    bare, backend_type = _bare_and_backend(model)
    if not bare or backend_type is None:
        return None
    _family, suffix_effort = parse_model_request(bare)
    identity = model_identity(model)
    if identity == UNKNOWN_MODEL_IDENTITY:
        return None
    knob_map = knobs or {}
    value: str | None = None
    for key in _EFFORT_KNOB_KEYS:
        raw_knob = knob_map.get(key)
        if raw_knob and str(raw_knob).strip():
            value = str(raw_knob).strip()
            break
    if value is None and suffix_effort is not None:
        value = suffix_effort
    if value is None:
        if backend_type == "cdp":
            family, _ = parse_model_request(bare)
            value = sealed_ask_default_effort(family)
        elif backend_type == "cursor_sdk":
            knob = effort_knob_name(identity)
            if knob is None:
                return None
            value = effective_knobs(identity, {}).get(knob) or default_variant(
                identity
            ).get(knob)
        elif backend_type == "cloud_api":
            return None
    return normalize_effort(value)


def consultant_identity(
    model: str, knobs: Mapping[str, str] | None = None
) -> ConsultantIdentity:
    """Pair the folded model identity with its measured effort rung."""
    return ConsultantIdentity(model_identity(model), consultant_rung(model, knobs))


def independently_measured(
    left: ConsultantIdentity, right: ConsultantIdentity
) -> bool:
    """True when two seats differ by model identity or by effort rung on the same model.

    ``UNKNOWN_MODEL_IDENTITY`` or an unmeasured rung (``None``) on either side
    never certifies independence — the predicate is fail-closed.
    """
    if UNKNOWN_MODEL_IDENTITY in (left.model_identity, right.model_identity):
        return False
    if left.model_identity != right.model_identity:
        return True
    if left.rung is None or right.rung is None:
        return False
    return left.rung != right.rung


def cursor_delivery_from_role(model: str) -> str | None:
    """Map cursor check/review model to gate-readable bus author role."""
    bare = ModelId.parse(model).api_model_id.lower()
    if bare.startswith("gpt-5.6") or bare == "gpt-5.5":
        return "reviewer"
    if bare.startswith("grok-4.6"):
        return "skeptic"
    return None


def is_cursor_check_review_model(model: str) -> bool:
    """True when ``model`` is in the standing cursor check/review allowlist."""
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
