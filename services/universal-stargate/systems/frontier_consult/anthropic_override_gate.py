"""Hard admission gate for explicit anthropic/* team_dispatch model overrides."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

from model_id import ModelId

CostIntent = Literal["deliberate_high_cost"] | None
SpawnReviewProvenance = Literal["generate_review_child"] | None

REJECT_CODE = "anthropic_override_requires_authorization"

CreditHealthProbe = Callable[[], bool]
_credit_health_probe: CreditHealthProbe | None = None


def set_anthropic_credit_health_probe(probe: CreditHealthProbe | None) -> None:
    """Inject or clear the credit-gate health probe (tests only)."""
    global _credit_health_probe
    _credit_health_probe = probe


def _default_credit_health_probe() -> bool:
    from transport_utils import DEFAULT_CLOUD_PROXY_URL, make_sync_client

    try:
        with make_sync_client(DEFAULT_CLOUD_PROXY_URL, timeout=2.0) as client:
            resp = client.get("/internal/anthropic-credit/status")
    except Exception:
        return False
    if resp.status_code != 200:
        return False
    try:
        body = resp.json()
    except (json.JSONDecodeError, ValueError):
        return False
    if not isinstance(body, dict):
        return False
    return body.get("healthy") is True


def _credit_gate_healthy() -> bool:
    probe = _credit_health_probe or _default_credit_health_probe
    try:
        return probe()
    except Exception:
        return False


def _standard_model_admit(model: str) -> bool:
    try:
        from anthropic_credit.config import CreditConfigInvalid, load_credit_config
    except ImportError:
        return False
    try:
        cfg = load_credit_config()
    except CreditConfigInvalid:
        return False
    canonical = ModelId.parse(model).normalized
    return canonical in cfg.standard_models and _credit_gate_healthy()


@dataclass(frozen=True, slots=True)
class AnthropicOverrideVerdict:
    admitted: bool
    code: str | None = None
    reason: str | None = None


def evaluate_anthropic_override(
    *,
    model: str,
    profile_provider: str,
    profile_allowed_models: tuple[str, ...] | frozenset[str],
    cost_intent: CostIntent = None,
    cost_intent_reason: str | None = None,
    spawn_review_provenance: SpawnReviewProvenance = None,
) -> AnthropicOverrideVerdict:
    """Return admit when the gate no-ops or an authorization path is satisfied."""
    if ModelId.parse(model).provider != "anthropic":
        return AnthropicOverrideVerdict(admitted=True)

    if profile_provider == "anthropic" and model in profile_allowed_models:
        return AnthropicOverrideVerdict(admitted=True)

    if spawn_review_provenance == "generate_review_child":
        return AnthropicOverrideVerdict(admitted=True)

    if (
        cost_intent == "deliberate_high_cost"
        and cost_intent_reason
        and cost_intent_reason.strip()
    ):
        return AnthropicOverrideVerdict(admitted=True)

    if _standard_model_admit(model):
        return AnthropicOverrideVerdict(admitted=True)

    return AnthropicOverrideVerdict(
        admitted=False,
        code=REJECT_CODE,
        reason=(
            f"Explicit anthropic model override {model!r} requires operator "
            "authorization: pass cost_intent=deliberate_high_cost with a "
            "non-empty cost_intent_reason, use an in-family anthropic profile "
            "with the model in profile allowed_models, or set "
            "spawn_review_provenance=generate_review_child for pipeline-encoded "
            "review-child spawns."
        ),
    )


def enforce_anthropic_override(
    *,
    request_id: str,
    model: str,
    profile_provider: str,
    profile_allowed_models: tuple[str, ...] | frozenset[str],
    cost_intent: CostIntent = None,
    cost_intent_reason: str | None = None,
    spawn_review_provenance: SpawnReviewProvenance = None,
) -> None:
    """Raise FrontierEndpointError when an explicit anthropic override lacks auth."""
    from .admission import FrontierEndpointError

    verdict = evaluate_anthropic_override(
        model=model,
        profile_provider=profile_provider,
        profile_allowed_models=profile_allowed_models,
        cost_intent=cost_intent,
        cost_intent_reason=cost_intent_reason,
        spawn_review_provenance=spawn_review_provenance,
    )
    if verdict.admitted:
        return
    raise FrontierEndpointError(
        request_id=request_id,
        field="model",
        reason=verdict.reason or "anthropic override requires authorization",
        status_code=422,
        code=verdict.code or REJECT_CODE,
    )


__all__ = [
    "AnthropicOverrideVerdict",
    "CostIntent",
    "REJECT_CODE",
    "SpawnReviewProvenance",
    "enforce_anthropic_override",
    "evaluate_anthropic_override",
    "set_anthropic_credit_health_probe",
]
