"""
Capacity detail extraction helpers for federated routing rejection.

Extracted from the former monolithic rejection.py during modularization.
Includes sticky-bound gateway detection and URL resolution for capacity failures.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Literal

from model_id import ModelId

if TYPE_CHECKING:
    from systems.proxy.core.nonstreaming.context import RequestContext
    from systems.routing.selection.types import SelectionTrace

_CAPACITY_CONSTRAINTS: frozenset[str] = frozenset(
    {
        "compute_type_capacity",
        "circuit_breaker",
        "eviction_blocked_by_busy_models",
        "has_enough_vram",
        "has_enough_ram",
    }
)

_EvictionDisposition = Literal["attempted", "refused", "not_applicable"]


def _sticky_bound_gateway_name(trace: "SelectionTrace") -> str | None:
    prefix = "sticky_capacity_wait: bound="
    reason = trace.selection_reason or ""
    if not reason.startswith(prefix):
        return None
    bound = reason.removeprefix(prefix).split(",", 1)[0].strip()
    return bound or None


def _candidate_has_capacity_failure(
    candidate: Any,
    all_capacity_constraints: set[str],
) -> bool:
    return any(
        failure.constraint in all_capacity_constraints
        for failure in candidate.constraints_failed
    )


def _capacity_gateway_url(candidate: Any, federated_gateways: list[Any]) -> str | None:
    gateway_ref_url = getattr(candidate.gateway.ref, "remote_stargate_url", None)
    if gateway_ref_url:
        return gateway_ref_url
    for federated_gateway in federated_gateways:
        if getattr(federated_gateway, "gateway_id", None) == candidate.gateway.name:
            return getattr(federated_gateway, "remote_stargate_url", None)
    return None


def _select_capacity_candidate(
    trace: "SelectionTrace",
    all_capacity_constraints: set[str],
) -> Any | None:
    capacity_candidates = [
        candidate
        for candidate in trace.candidates
        if _candidate_has_capacity_failure(candidate, all_capacity_constraints)
    ]
    if not capacity_candidates:
        return None
    bound_gateway = _sticky_bound_gateway_name(trace)
    return next(
        (
            candidate
            for candidate in capacity_candidates
            if candidate.gateway.name == bound_gateway
        ),
        capacity_candidates[0],
    )


def _routing_keys_on_gateway(gateway: Any) -> dict[str, list[str]]:
    loaded = sorted(mid.routing_key for mid in gateway.loaded_models)
    busy = sorted(mid.routing_key for mid in gateway.busy_models)
    loading = sorted(mid.routing_key for mid in gateway.loading_models)
    return {"loaded": loaded, "busy": busy, "loading": loading}


def _primary_capacity_failure(candidate: Any) -> Any | None:
    for failure in candidate.constraints_failed:
        if failure.constraint in _CAPACITY_CONSTRAINTS:
            return failure
    return None


def _eviction_block(
    candidate: Any | None,
) -> dict[str, Any]:
    if candidate is None:
        return {
            "disposition": "not_applicable",
            "constraint": None,
            "reason": "no_capacity_candidate_on_trace",
        }
    plan = getattr(candidate, "eviction_plan", None)
    if plan is not None and getattr(plan, "models_to_evict", None):
        victims = sorted(mid.routing_key for mid in plan.models_to_evict)
        return {
            "disposition": "attempted",
            "constraint": "eviction_plan",
            "reason": f"planned_victims={victims}",
        }
    failure = _primary_capacity_failure(candidate)
    if failure is None:
        return {
            "disposition": "not_applicable",
            "constraint": None,
            "reason": "no_capacity_constraint_on_candidate",
        }
    constraint = failure.constraint
    reason = failure.reason or constraint
    if constraint == "eviction_blocked_by_busy_models":
        disposition: _EvictionDisposition = "refused"
    else:
        disposition = "not_applicable"
    return {
        "disposition": disposition,
        "constraint": constraint,
        "reason": reason,
    }


def _retry_guidance_text(
    *,
    budget_exhausted: bool,
    eviction: dict[str, Any],
    failure_reason: str | None,
) -> str:
    if failure_reason == "eviction_requeue_depth_exceeded":
        return (
            "Nested eviction requeues hit the configured depth cap; "
            "retry later or reduce concurrent cold loads on this gateway."
        )
    if budget_exhausted:
        return (
            "Eviction wait budget exhausted; retry after holders drain or "
            "raise eviction_wait_timeout_s if sustained overload is expected."
        )
    if eviction.get("disposition") == "refused":
        return (
            "Eviction blocked by busy loaded models; retry after in-flight "
            "requests complete or pause competing models."
        )
    return (
        "Transient sticky capacity contention; retry with backoff while "
        "gateways drain or evict idle models."
    )


def _format_holder_summary(holders: dict[str, Any]) -> str:
    if holders.get("holders_unknown"):
        return f"holders_unknown ({holders.get('holders_unknown_reason', 'unknown')})"
    loaded = holders.get("loaded") or []
    busy = holders.get("busy") or []
    loading = holders.get("loading") or []
    parts: list[str] = []
    if loaded:
        parts.append(f"loaded={loaded}")
    if busy:
        parts.append(f"busy={busy}")
    if loading:
        parts.append(f"loading={loading}")
    if not parts:
        return "holders=none"
    return "; ".join(parts)


def format_sticky_capacity_message(model_routing_key: str, data: dict[str, Any]) -> str:
    """One-line HTTP message for OpenAI-shape clients that omit error data."""
    holders = data.get("holders")
    holder_line = _format_holder_summary(holders if isinstance(holders, dict) else {})
    eviction = data.get("eviction") or {}
    eviction_line = (
        f"eviction={eviction.get('disposition', 'unknown')}"
        f" ({eviction.get('constraint') or 'n/a'}: {eviction.get('reason') or 'n/a'})"
    )
    waited_ms = data.get("waited_ms")
    budget_s = data.get("wait_budget_s")
    budget_ms = int(float(budget_s) * 1000) if budget_s is not None else "unknown"
    wait_line = f"waited_ms={waited_ms}/{budget_ms}ms budget_exhausted={data.get('budget_exhausted')}"
    depth_line = ""
    if (
        data.get("requeue_depth") is not None
        and data.get("requeue_max_depth") is not None
    ):
        depth_line = (
            f"requeue_depth={data['requeue_depth']}/"
            f"{data['requeue_max_depth']}; "
        )
    return (
        f"Sticky model {model_routing_key}: {holder_line}; "
        f"{eviction_line}; {depth_line}{wait_line}"
    )


def build_sticky_capacity_failure_summary(
    *,
    model: ModelId | str,
    trace: "SelectionTrace | None",
    routing_config: dict[str, Any] | None,
    context: "RequestContext | None" = None,
    federated_gateways: list[Any] | None = None,
    waited_ms: int | None = None,
    failure_reason: str | None = None,
    requeue_depth: int | None = None,
    requeue_max_depth: int | None = None,
    budget_exhausted: bool | None = None,
    all_capacity_constraints: set[str] | None = None,
) -> dict[str, Any]:
    """Structured STICKY_CAPACITY payload shared by all raise sites."""
    from systems.proxy.core.nonstreaming.federated_routing.wait_continuation import (
        arm_or_remaining_eviction_budget,
        get_eviction_wait_budget_s,
    )

    parsed = model if isinstance(model, ModelId) else ModelId.parse(model)
    model_routing_key = parsed.routing_key
    constraint_set = all_capacity_constraints or set(_CAPACITY_CONSTRAINTS)
    federated_gateways = federated_gateways or []

    wait_budget_s = get_eviction_wait_budget_s(routing_config)
    if waited_ms is None:
        waited_ms = 0

    candidate = None
    gateway_id: str | None = None
    if trace is not None and getattr(trace, "candidates", None):
        candidate = _select_capacity_candidate(trace, constraint_set)
        if candidate is not None:
            gateway_id = candidate.gateway.name
        elif _sticky_bound_gateway_name(trace):
            gateway_id = _sticky_bound_gateway_name(trace)

    if budget_exhausted is None:
        remaining_s = (
            arm_or_remaining_eviction_budget(context, wait_budget_s)
            if context is not None
            else wait_budget_s
        )
        budget_exhausted = remaining_s == 0.0 or (
            wait_budget_s > 0 and waited_ms >= int(wait_budget_s * 1000) - 1
        )

    if candidate is not None:
        holders_payload: dict[str, Any] = _routing_keys_on_gateway(candidate.gateway)
    elif gateway_id and trace is not None:
        holders_payload = {
            "holders_unknown": True,
            "holders_unknown_reason": (
                "bound_gateway_named_on_trace_but_no_capacity_candidate"
            ),
        }
    else:
        holders_payload = {
            "holders_unknown": True,
            "holders_unknown_reason": "no_trace_or_bound_gateway",
        }

    eviction = _eviction_block(candidate)
    retry_guidance = _retry_guidance_text(
        budget_exhausted=bool(budget_exhausted),
        eviction=eviction,
        failure_reason=failure_reason,
    )

    data: dict[str, Any] = {
        "model_id": model_routing_key,
        "gateway_id": gateway_id,
        "holders": holders_payload,
        "eviction": eviction,
        "wait_budget_s": wait_budget_s,
        "waited_ms": waited_ms,
        "budget_exhausted": bool(budget_exhausted),
        "retry_guidance": retry_guidance,
    }

    if failure_reason is not None:
        data["reason"] = failure_reason

    if requeue_depth is not None:
        data["requeue_depth"] = requeue_depth
    if requeue_max_depth is not None:
        data["requeue_max_depth"] = requeue_max_depth

    if trace is not None and candidate is not None:
        legacy = _legacy_capacity_fields(
            model_routing_key,
            trace,
            constraint_set,
            federated_gateways,
            candidate,
        )
        data.update(legacy)

    return data


def _legacy_capacity_fields(
    model_routing_key: str,
    trace: "SelectionTrace",
    all_capacity_constraints: set[str],
    federated_gateways: list[Any],
    candidate: Any,
) -> dict[str, Any]:
    """Preserve constraint detail keys used before envelope enrichment."""
    capacity_details: dict[str, Any] = {"model_id": model_routing_key}
    for failure in candidate.constraints_failed:
        if failure.constraint in all_capacity_constraints:
            capacity_details.update(failure.details)
            break
    capacity_details["gateway_id"] = candidate.gateway.name
    gateway_url = _capacity_gateway_url(candidate, federated_gateways)
    if gateway_url:
        capacity_details["gateway_url"] = gateway_url
    return capacity_details


def _build_capacity_details(
    model_id: ModelId | str,
    trace: "SelectionTrace",
    all_capacity_constraints: set[str],
    federated_gateways: list[Any],
) -> dict[str, Any]:
    """Backward-compatible capacity details for callers that only need trace fields."""
    parsed = model_id if isinstance(model_id, ModelId) else ModelId.parse(model_id)
    candidate = _select_capacity_candidate(trace, all_capacity_constraints)
    if candidate is None:
        return {"model_id": parsed.routing_key}
    return _legacy_capacity_fields(
        parsed.routing_key,
        trace,
        all_capacity_constraints,
        federated_gateways,
        candidate,
    )