"""Hard admission gate for explicit anthropic/* team_dispatch model overrides."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

import httpx
from model_id import ModelId

CostIntent = Literal["deliberate_high_cost"] | None
SpawnReviewProvenance = Literal["generate_review_child"] | None

REJECT_CODE = "anthropic_override_requires_authorization"

_logger = logging.getLogger(__name__)

CreditHealthProbe = Callable[[], bool]
_credit_health_probe: CreditHealthProbe | None = None

_HEALTHY_TTL_S = 5.0
_UNHEALTHY_TTL_S = 1.0
_cache_lock = asyncio.Lock()
_cache_value: bool | None = None
_cache_expires_monotonic = 0.0
_cloud_proxy_url_resolved: str | None = None


def reset_configured_cloud_proxy_url_cache() -> None:
    """Clear resolved cloud-proxy URL (tests)."""
    global _cloud_proxy_url_resolved
    _cloud_proxy_url_resolved = None


def set_anthropic_credit_health_probe(probe: CreditHealthProbe | None) -> None:
    """Inject or clear the credit-gate health probe (tests only)."""
    global _credit_health_probe
    _credit_health_probe = probe
    reset_credit_health_cache()


def reset_credit_health_cache() -> None:
    """Clear async health cache (tests)."""
    global _cache_value, _cache_expires_monotonic
    _cache_value = None
    _cache_expires_monotonic = 0.0


def _configured_cloud_proxy_url() -> str:
    global _cloud_proxy_url_resolved
    if _cloud_proxy_url_resolved is not None:
        return _cloud_proxy_url_resolved
    from transport_utils import DEFAULT_CLOUD_PROXY_URL

    url = DEFAULT_CLOUD_PROXY_URL.strip().rstrip("/")
    try:
        from systems.cloud.config import parse_cloud_proxy_config
        from systems.proxy.stargate_config.config import StargateConfig

        raw = StargateConfig().get_cloud_proxy_config()
        parsed = parse_cloud_proxy_config(raw)
        if parsed and parsed.url:
            url = parsed.url.strip().rstrip("/")
    except Exception:
        pass
    _cloud_proxy_url_resolved = url
    return url


def _status_body_healthy(body: object) -> bool:
    if not isinstance(body, dict):
        return False
    return body.get("healthy") is True


async def _default_async_credit_health_probe() -> bool:
    from transport_utils import make_async_client

    url = _configured_cloud_proxy_url()
    try:
        async with make_async_client(url, timeout=httpx.Timeout(1.0)) as client:
            resp = await client.get("/internal/anthropic-credit/status")
    except Exception:
        return False
    if resp.status_code != 200:
        return False
    try:
        body = resp.json()
    except (json.JSONDecodeError, ValueError):
        return False
    return _status_body_healthy(body)


async def credit_gate_healthy() -> bool:
    """Cached async probe of cloud_proxy anthropic-credit status (fail-closed)."""
    global _cache_value, _cache_expires_monotonic
    if _credit_health_probe is not None:
        try:
            return bool(_credit_health_probe())
        except Exception:
            return False
    now = time.monotonic()
    if _cache_value is not None and now < _cache_expires_monotonic:
        return _cache_value
    async with _cache_lock:
        now = time.monotonic()
        if _cache_value is not None and now < _cache_expires_monotonic:
            return _cache_value
        healthy = await _default_async_credit_health_probe()
        ttl = _HEALTHY_TTL_S if healthy else _UNHEALTHY_TTL_S
        _cache_value = healthy
        _cache_expires_monotonic = time.monotonic() + ttl
        return healthy


def _standard_model_admit(
    model: str,
    *,
    credit_healthy: bool,
    standard_models: frozenset[str],
) -> bool:
    parsed = ModelId.parse(model)
    if parsed.routing_layer is not None or parsed.backend_type != "cloud_api":
        return False
    canonical = parsed.normalized
    return canonical in standard_models and credit_healthy


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
    credit_healthy: bool = False,
    standard_models: frozenset[str] = frozenset(),
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

    if _standard_model_admit(
        model, credit_healthy=credit_healthy, standard_models=standard_models
    ):
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
    credit_healthy: bool = False,
    standard_models: frozenset[str] = frozenset(),
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
        credit_healthy=credit_healthy,
        standard_models=standard_models,
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
    "credit_gate_healthy",
    "enforce_anthropic_override",
    "evaluate_anthropic_override",
    "reset_configured_cloud_proxy_url_cache",
    "reset_credit_health_cache",
    "set_anthropic_credit_health_probe",
]
