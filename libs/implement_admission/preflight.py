"""Phase 2 preflight gates — decision entity must exist before live routing."""

from __future__ import annotations

from typing import Any, Protocol


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
