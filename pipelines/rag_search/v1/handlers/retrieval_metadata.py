"""L1 retrieval metadata step."""

from __future__ import annotations

from typing import TYPE_CHECKING, override

from systems.pipeline.core.handlers.builtin import BaseHandler
from systems.pipeline.core.handlers.protocol import StepOutput

if TYPE_CHECKING:
    from systems.pipeline.core.handlers.protocol import PipelineContext
    from systems.pipeline.core.schemas import StepConfig


class RetrievalMetadataHandler(BaseHandler):
    """Record which steps ran. step_type matches the registration key."""

    step_type = "retrieval_metadata_step"

    @override
    async def execute(self, step: StepConfig, context: PipelineContext) -> StepOutput:
        completed = sorted(context.outputs.keys())
        return StepOutput(raw="", json={"steps_completed": completed})
