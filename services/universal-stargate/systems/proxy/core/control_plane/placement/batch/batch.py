"""
Batch abstractions and factory methods for coordinated routing.
"""

from dataclasses import dataclass, field
from typing import Any

from model_id import ModelId
from universal_logging import get_logger

from .request import InferenceRequest, RequestOrigin
from .requirements import MAX_BATCH_SIZE

logger = get_logger(__name__)


@dataclass(slots=True)
class InferenceBatch:
    """
    A batch of related requests to route atomically.

    For pipelines: parallel steps form a batch.
    For standalone: batch of 1.

    Invariants:
    - ∀ request ∈ requests: request.batch_id = batch_id
    - total_vram_mb = Σ(r.vram_required_mb for r in requests if r.is_gpu)
    - total_ram_mb = Σ(r.ram_required_mb for r in requests if ¬r.is_gpu)
    - |requests| ≤ MAX_BATCH_SIZE

    Lifecycle:
    1. Factory creates batch with requests + aggregated resources
    2. BatchRouter (Phase 2) populates gateway_assignments and deferred_requests
    3. Executor (Phase 3) uses assignments to route requests
    """

    batch_id: str
    requests: list[InferenceRequest] = field(default_factory=list)

    # Aggregated requirements (computed at creation, immutable after)
    total_vram_mb: int = 0
    total_ram_mb: int = 0

    # Routing results (mutable, populated by BatchRouter in Phase 2)
    # BatchRouter will populate these fields after making routing decisions:
    # - gateway_assignments: Maps request_id -> gateway_name for routed requests
    # - deferred_requests: List of request_ids that couldn't be assigned
    gateway_assignments: dict[str, str] = field(default_factory=dict)
    deferred_requests: list[str] = field(default_factory=list)

    # Federated gateway assignments (populated by BatchRouter for federated routes)
    # Maps request_id -> Gateway object for federated handling
    federated_assignments: dict[str, Any] = field(default_factory=dict)

    # Optional metadata for scheduling context (pipeline batches)
    metadata: dict[str, Any] | None = field(default=None)

    def __post_init__(self) -> None:
        """Validate batch constraints."""
        # Allow empty batches (for pipelines with only non-model steps)
        if not self.requests:
            logger.debug(
                f"Created empty batch {self.batch_id} (no model-invoking steps)"
            )
            return

        if len(self.requests) > MAX_BATCH_SIZE:
            raise ValueError(
                f"Batch size {len(self.requests)} exceeds maximum {MAX_BATCH_SIZE}"
            )

        # Validate all requests share same batch_id
        for request in self.requests:
            if request.batch_id != self.batch_id:
                raise ValueError(
                    f"Request {request.request_id} has batch_id={request.batch_id}, "
                    f"expected {self.batch_id}"
                )

    @property
    def is_standalone(self) -> bool:
        """True if this is a single standalone request."""
        return (
            len(self.requests) == 1
            and self.requests[0].origin == RequestOrigin.STANDALONE
        )

    @property
    def is_pipeline_batch(self) -> bool:
        """True if this contains pipeline step requests."""
        return any(r.origin == RequestOrigin.PIPELINE_STEP for r in self.requests)

    @property
    def unassigned_requests(self) -> list[InferenceRequest]:
        """Requests not yet assigned to a gateway."""
        return [
            r
            for r in self.requests
            if r.request_id not in self.gateway_assignments
            and r.request_id not in self.deferred_requests
        ]

    def __repr__(self) -> str:
        """Debugging representation."""
        origin = "standalone" if self.is_standalone else "pipeline"
        return (
            f"InferenceBatch(id={self.batch_id}, size={len(self.requests)}, "
            f"origin={origin}, vram={self.total_vram_mb}MB, ram={self.total_ram_mb}MB)"
        )


class InferenceBatchFactory:
    """
    Factory for creating InferenceBatch objects.

    Separated from dataclass for cleaner dependencies.

    Note: The from_pipeline_steps() method has been REMOVED to maintain
    domain isolation. Pipeline now uses HTTP to invoke models directly.
    This keeps the proxy domain generic and reusable.
    """

    @staticmethod
    def standalone(
        request_id: str,
        model_id: ModelId,
        vram_mb: int = 0,
        ram_mb: int = 0,
        is_gpu: bool = True,
        sticky: bool = True,
    ) -> InferenceBatch:
        """
        Create single-request batch for standalone API calls.

        Fast path: no pipeline coordination needed.

        Args:
            request_id: Unique request identifier
            model_id: Model to route to
            vram_mb: VRAM required (0 = unknown, will lookup at routing)
            ram_mb: RAM required (0 = unknown, will lookup at routing)
            is_gpu: Whether this is GPU inference
            sticky: Use sticky routing (default: True)

        Returns:
            InferenceBatch with single request
        """
        request = InferenceRequest(
            request_id=request_id,
            model_id=model_id,
            origin=RequestOrigin.STANDALONE,
            batch_id=request_id,  # Batch ID = request ID for standalone
            vram_required_mb=vram_mb,
            ram_required_mb=ram_mb,
            is_gpu=is_gpu,
            sticky=sticky,
        )

        return InferenceBatch(
            batch_id=request_id,
            requests=[request],
            total_vram_mb=vram_mb if is_gpu else 0,
            total_ram_mb=ram_mb if not is_gpu else 0,
        )

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> InferenceBatch:
        """
        Create batch from generic dict (domain-agnostic).

        This method maintains domain isolation by accepting generic data structures
        instead of domain-specific types.

        Args:
            data: Dict with 'requests', 'batch_id', 'total_vram_mb', 'total_ram_mb'
                  Optional: 'scheduling_context' for pipeline batches

        Returns:
            InferenceBatch with populated requests and metadata
        """
        requests = []
        for r in data.get("requests", []):
            requests.append(
                InferenceRequest(
                    request_id=r["request_id"],
                    model_id=ModelId.parse(r["model_id"]),
                    origin=RequestOrigin.PIPELINE_STEP
                    if r.get("pipeline_id")
                    else RequestOrigin.STANDALONE,
                    pipeline_id=r.get("pipeline_id"),
                    step_id=r.get("step_id"),
                    batch_id=data["batch_id"],
                    vram_required_mb=r.get("vram_mb", 0),
                    ram_required_mb=r.get("ram_mb", 0),
                    is_gpu=r.get("is_gpu", True),
                    sticky=r.get("sticky", False),
                )
            )

        # Extract metadata (e.g., scheduling_context for pipeline batches)
        metadata = None
        if "scheduling_context" in data:
            metadata = {"scheduling_context": data["scheduling_context"]}

        return InferenceBatch(
            batch_id=data["batch_id"],
            requests=requests,
            total_vram_mb=data.get("total_vram_mb", 0),
            total_ram_mb=data.get("total_ram_mb", 0),
            metadata=metadata,
        )
