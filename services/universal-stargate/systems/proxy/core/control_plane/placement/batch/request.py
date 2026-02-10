"""
Request abstractions for pipeline-aware routing.
"""

from dataclasses import dataclass
from enum import StrEnum, auto

from model_id import ModelId


class RequestOrigin(StrEnum):
    """
    Origin context for inference requests.

    Determines routing behavior:
    - STANDALONE: Independent request, uses sticky routing (single gateway)
    - PIPELINE_STEP: Part of pipeline, coordinated routing (can split across gateways)

    Routing implications:
    - STANDALONE: Sticky routing ensures model loaded on one gateway
    - PIPELINE_STEP: Non-sticky routing allows parallel execution across gateways
    """

    STANDALONE = auto()
    PIPELINE_STEP = auto()


@dataclass(frozen=True, slots=True)
class InferenceRequest:
    """
    Unified request object carrying routing context.

    Immutable after creation. Carries enough context for BatchRouter
    to make coordinated routing decisions.

    Invariants:
    - origin = PIPELINE_STEP ⟹ pipeline_id ≠ None ∧ step_id ≠ None
    - origin = STANDALONE ⟹ pipeline_id = None
    - batch_id links requests routed together
    """

    # Identity
    request_id: str
    model_id: ModelId

    # Origin context
    origin: RequestOrigin = RequestOrigin.STANDALONE
    pipeline_id: str | None = None
    step_id: str | None = None
    batch_id: str | None = None

    # Resource requirements (from catalog)
    vram_required_mb: int = 0
    ram_required_mb: int = 0
    is_gpu: bool = True

    # Routing preferences
    sticky: bool = True
    preferred_gateway: str | None = None

    def __post_init__(self) -> None:
        """Validate invariants."""
        if self.origin == RequestOrigin.PIPELINE_STEP:
            if self.pipeline_id is None or self.step_id is None:
                raise ValueError(
                    "Pipeline step requests require pipeline_id and step_id"
                )
        elif self.origin == RequestOrigin.STANDALONE:
            if self.pipeline_id is not None:
                raise ValueError("Standalone requests must not have pipeline_id")

    def __repr__(self) -> str:
        """Debugging representation."""
        return (
            f"InferenceRequest(id={self.request_id}, model={self.model_id}, "
            f"origin={self.origin.value}, batch={self.batch_id})"
        )
