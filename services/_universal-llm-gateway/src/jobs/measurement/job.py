"""
Measurement job for VRAM/RAM profiling.

Smart context detection:
- GPU mode: starts at training_context_length, steps down until it fits on GPU
- CPU mode: uses training_context_length
- Auto mode: tries GPU first with step-down, falls back to CPU
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, override

from ..job import Job
from .request import MeasureJobRequest
from .run_loop import run_measurement_job

__all__ = ["MeasureJobRequest", "MeasurementJob"]

if TYPE_CHECKING:
    from ...core.gateway_config import GatewayConfig


@dataclass
class MeasurementJob(Job):
    """
    Measurement job for VRAM/RAM profiling.

    Runs measurement subprocess for each context size and updates catalog.
    Pre-loaded gateway configuration ensures no blocking I/O in async path.
    """

    request: MeasureJobRequest = field(
        default_factory=lambda: MeasureJobRequest(model_id="")
    )
    gateway_config: "GatewayConfig | None" = None
    job_type: str = field(default="measure", init=False)

    @override
    async def _run(self) -> None:
        """Execute measurement job."""
        if not self.gateway_config:
            raise RuntimeError("gateway_config must be provided at job construction")

        await run_measurement_job(
            self.request,
            self.gateway_config,
            self.emit_log,
            lambda result: setattr(self, "result", result),
        )

    async def _resolve_model_path(self) -> Path | None:
        """Resolve model ID to file path (GGUF) or directory (vLLM)."""
        from ..context_detection import resolve_model_path

        return resolve_model_path(self.request.model_id)
