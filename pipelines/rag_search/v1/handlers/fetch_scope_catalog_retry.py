"""Catalog retry step for rag-search. New role, suffix _v1."""

from __future__ import annotations

from typing import TYPE_CHECKING, override

from systems.pipeline.core.handlers.builtin import BaseHandler
from systems.pipeline.core.handlers.protocol import StepOutput

if TYPE_CHECKING:
    from systems.pipeline.core.handlers.protocol import PipelineContext
    from systems.pipeline.core.schemas import StepConfig


class FetchScopeCatalogRetryHandler(BaseHandler):
    step_type = "fetch_scope_catalog_retry_v1"

    @override
    async def execute(self, step: StepConfig, context: PipelineContext) -> StepOutput:
        return StepOutput(raw="", json={"catalog_retry": True})
