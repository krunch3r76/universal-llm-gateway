"""Phase 2 preflight gates — decision entity + route_contract enforcement."""

from __future__ import annotations

import re
from typing import Any, Protocol

from implement_admission.spec import ImplementSpec, RouteContract

_PROSE_CONTRADICTION_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\bmanual\s+ide\s+pickup\b", re.IGNORECASE), "manual IDE pickup"),
    (
        re.compile(r"\bmanual\s+pickup\s+required\b", re.IGNORECASE),
        "manual pickup required",
    ),
)
_MANUAL_REVIEW = re.compile(r"\bmanual\s+review\b", re.IGNORECASE)

# Non-team_dispatch transports: must run route preflight or stay explicitly deprecated.
DISPATCH_TRANSPORT_REGISTRY: dict[str, dict[str, str]] = {
    "team_dispatch": {
        "status": "active",
        "preflight": "run_route_preflight",
    },
    "frontier_dispatch": {
        "status": "deprecated",
        "note": (
            "HTTP-only internal persona-free dispatch; agents must use MCP team_dispatch"
        ),
    },
    "panel_dispatch": {
        "status": "active",
        "note": "consensus panels — no implement route_contract; not a bypass for implement",
    },
    "pipeline_dispatch": {
        "status": "active",
        "note": "pipeline-owned delivery after team_dispatch admission",
    },
}


class CortexReader(Protocol):
    def assertion_state(self, entity_id: str, **kwargs: Any) -> dict[str, Any]: ...


class DecisionNotAssertedError(Exception):
    """Raised when decision:unified-implement-admission lacks confirmed ratification."""

    entity_id: str = "decision:unified-implement-admission"

    def __init__(self, message: str | None = None) -> None:
        super().__init__(
            message
            or (
                f"{self.entity_id} has no confirmed non-superseded assertion; "
                "operator must ratify via Menu-D before Phase 2 live routing"
            )
        )


class RouteContractContradictionError(Exception):
    """Structured caller field contradicts canonical route_contract."""

    code: str = "route_contract_contradiction"

    def __init__(
        self,
        *,
        field: str,
        caller_value: Any,
        canonical_value: Any,
    ) -> None:
        self.field = field
        self.caller_value = caller_value
        self.canonical_value = canonical_value
        super().__init__(
            f"{field}={caller_value!r} contradicts canonical route_contract "
            f"{field}={canonical_value!r}"
        )


def require_decision_asserted(*, cortex: CortexReader) -> None:
    """Fail closed if the unified-implement-admission decision is not asserted."""
    entity_id = DecisionNotAssertedError.entity_id
    try:
        result = cortex.assertion_state(entity_id)
    except Exception as exc:
        raise DecisionNotAssertedError(f"could not load {entity_id}: {exc}") from exc

    if result.get("error"):
        raise DecisionNotAssertedError(
            f"assertion_state failed for {entity_id}: {result['error']}"
        )
    if not result.get("ratified"):
        raise DecisionNotAssertedError()


def check_structured_route_contradictions(
    route_contract: RouteContract,
    *,
    operator_pickup_required: bool | None = None,
    autonomy: str | None = None,
    transport: str | None = None,
) -> None:
    """Reject when caller-supplied structured fields contradict canonical policy."""
    if (
        operator_pickup_required is not None
        and operator_pickup_required != route_contract.operator_pickup_required
    ):
        raise RouteContractContradictionError(
            field="operator_pickup_required",
            caller_value=operator_pickup_required,
            canonical_value=route_contract.operator_pickup_required,
        )
    if autonomy is not None and autonomy != route_contract.autonomy:
        raise RouteContractContradictionError(
            field="autonomy",
            caller_value=autonomy,
            canonical_value=route_contract.autonomy,
        )
    if transport is not None and transport != route_contract.transport:
        raise RouteContractContradictionError(
            field="transport",
            caller_value=transport,
            canonical_value=route_contract.transport,
        )


def lint_prose_route_contradictions(
    packet_text: str,
    route_contract: RouteContract,
) -> list[str]:
    """Conservative prose lint — warn only when autonomy is auto_executed."""
    if route_contract.autonomy != "auto_executed" or not packet_text:
        return []

    warnings: list[str] = []
    for pattern, label in _PROSE_CONTRADICTION_PATTERNS:
        for match in pattern.finditer(packet_text):
            start, end = match.span()
            window = packet_text[max(0, start - 20) : min(len(packet_text), end + 20)]
            if _MANUAL_REVIEW.search(window):
                continue
            warnings.append(
                "route_contract.prose_contradiction: packet prose "
                f"{label!r} while autonomy=auto_executed (warning only)"
            )
    return warnings


def run_route_preflight(
    spec: ImplementSpec,
    *,
    operator_pickup_required: bool | None = None,
    autonomy: str | None = None,
    transport: str | None = None,
    packet_text: str | None = None,
) -> list[str]:
    """Structured reject + conservative prose warnings for route_contract."""
    route_contract = spec.route_contract
    if route_contract is None:
        return []
    check_structured_route_contradictions(
        route_contract,
        operator_pickup_required=operator_pickup_required,
        autonomy=autonomy,
        transport=transport,
    )
    return lint_prose_route_contradictions(packet_text or "", route_contract)


def admission_route_contract_payload(spec: ImplementSpec) -> dict[str, Any]:
    """Serialize route_contract for team_dispatch admission responses."""
    if spec.route_contract is None:
        return {}
    return {"route_contract": spec.route_contract.model_dump()}


def verify_dispatch_transport_coverage() -> list[str]:
    """Return transport slugs lacking preflight or explicit deprecation."""
    gaps: list[str] = []
    for slug, meta in DISPATCH_TRANSPORT_REGISTRY.items():
        if meta.get("status") == "deprecated":
            continue
        if meta.get("preflight") or meta.get("note"):
            continue
        gaps.append(slug)
    return gaps
