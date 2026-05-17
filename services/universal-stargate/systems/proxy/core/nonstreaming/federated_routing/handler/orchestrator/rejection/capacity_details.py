"""
Capacity detail extraction helpers for federated routing rejection.

Extracted from the former monolithic rejection.py during modularization.
Includes sticky-bound gateway detection and URL resolution for capacity failures.
"""

from typing import TYPE_CHECKING, Any

from model_id import ModelId

if TYPE_CHECKING:
    from systems.routing.selection.types import SelectionTrace


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


def _build_capacity_details(
    model_id: ModelId | str,
    trace: "SelectionTrace",
    all_capacity_constraints: set[str],
    federated_gateways: list[Any],
) -> dict[str, Any]:
    capacity_details: dict[str, Any] = {"model_id": ModelId.parse(model_id).routing_key}
    capacity_candidates = [
        candidate
        for candidate in trace.candidates
        if _candidate_has_capacity_failure(candidate, all_capacity_constraints)
    ]
    if not capacity_candidates:
        return capacity_details

    bound_gateway = _sticky_bound_gateway_name(trace)
    candidate = next(
        (
            candidate
            for candidate in capacity_candidates
            if candidate.gateway.name == bound_gateway
        ),
        capacity_candidates[0],
    )

    for failure in candidate.constraints_failed:
        if failure.constraint in all_capacity_constraints:
            capacity_details.update(failure.details)
            break

    capacity_details["gateway_id"] = candidate.gateway.name
    gateway_url = _capacity_gateway_url(candidate, federated_gateways)
    if gateway_url:
        capacity_details["gateway_url"] = gateway_url
    return capacity_details
