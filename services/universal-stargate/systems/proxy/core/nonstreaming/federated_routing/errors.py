"""
Constraint summary helpers for federated routing rejection and infeasibility paths.

These helpers keep error payload construction deterministic so retry and client-side
diagnostics can distinguish exclusion, feasibility, and resource failure boundaries.
"""

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from systems.federation.common.types import FederatedGateway

    from ..context import RequestContext


def _build_constraint_summary(
    trace: Any | None,
    federated_gateways: list["FederatedGateway"],
    context: "RequestContext",
) -> dict[str, Any]:
    """
    Build a stable per-gateway failed-constraint summary for routing envelopes.
    """
    summary: dict[str, Any] = {}
    if context.excluded_gateway_ids:
        summary["excluded_gateways"] = list(context.excluded_gateway_ids)
    if trace and trace.candidates:
        summary["gateway_failures"] = [
            {
                "gateway": c.gateway.name,
                "constraints": [
                    {"constraint": f.constraint, "reason": f.reason}
                    for f in c.constraints_failed
                ],
            }
            for c in trace.candidates
            if c.constraints_failed
        ]
    return summary
