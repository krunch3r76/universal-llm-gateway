"""Rerank step for rag-search. New role, suffix _v1."""

from __future__ import annotations

from typing import TYPE_CHECKING, override

from systems.pipeline.core.handlers.builtin import BaseHandler
from systems.pipeline.core.handlers.protocol import StepOutput

if TYPE_CHECKING:
    from systems.pipeline.core.handlers.protocol import PipelineContext
    from systems.pipeline.core.schemas import StepConfig


class RerankHandler(BaseHandler):
    step_type = "rerank_v1"

    @override
    async def execute(self, step: StepConfig, context: PipelineContext) -> StepOutput:
        return StepOutput(raw="", json={"reranked": True})
