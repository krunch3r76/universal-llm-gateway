"""
pipeline_call_v1 step handler.

Calls another pipeline as a service via Stargate's chat completions endpoint.
Enables domain pipelines to use retrieval service pipelines (e.g. rag-context)
as a step without duplicating RAG logic inline.

Domain fields (from pipeline YAML step config):
    pipeline_id: str  — virtual model ID of the pipeline to call (required)
    stargate_url: str — Stargate base URL (default: http://localhost:9999)
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, override

import httpx
from systems.pipeline.core.handlers.protocol import AbstractStepHandler, StepOutput
from universal_logging import get_logger

if TYPE_CHECKING:
    from systems.pipeline.core.handlers.protocol import PipelineContext
    from systems.pipeline.core.schemas import StepConfig

logger = get_logger(__name__)

DEFAULT_STARGATE_URL = "http://localhost:9999"
DEFAULT_TIMEOUT = 60.0


class PipelineCallHandler(AbstractStepHandler):
    """
    Call a pipeline by its virtual model ID via Stargate.

    Sends context.source_text as the user message and returns the
    pipeline's response as StepOutput.raw. Enables domain pipelines to
    delegate to service pipelines (e.g. rag-context for retrieval) without
    owning their logic.

    Invariants:
    - ∀ execute(): pipeline_id field is required
    - ∀ response: StepOutput.raw = pipeline response text
    - ∀ empty response: returns sentinel "No context returned by pipeline"
    """

    step_type: str = "pipeline_call_v1"

    @override
    async def execute(
        self,
        step: StepConfig,
        context: PipelineContext,
    ) -> StepOutput:
        pipeline_id: str | None = step.get_domain_field("pipeline_id")
        if not pipeline_id:
            raise ValueError(f"Step '{step.id}': missing required 'pipeline_id' field")

        stargate_url: str = step.get_domain_field("stargate_url", DEFAULT_STARGATE_URL)
        url = f"{stargate_url.rstrip('/')}/v1/chat/completions"

        body = {
            "model": pipeline_id,
            "messages": [{"role": "user", "content": context.source_text}],
            "stream": False,
        }

        start = time.monotonic()
        async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as client:
            response = await client.post(url, json=body)
            response.raise_for_status()
        latency_ms = (time.monotonic() - start) * 1000

        data = response.json()
        content: str = (
            data.get("choices", [{}])[0].get("message", {}).get("content", "")
        )

        if not content.strip():
            content = f"No context returned by pipeline '{pipeline_id}'"

        usage = data.get("usage", {})

        logger.debug(
            "pipeline_call_v1 '%s': pipeline_id=%r, latency=%.0fms",
            step.id,
            pipeline_id,
            latency_ms,
        )

        return StepOutput(
            raw=content,
            prompt_tokens=usage.get("prompt_tokens", 0),
            completion_tokens=usage.get("completion_tokens", 0),
            latency_ms=latency_ms,
        )

    @override
    def validate(self, step: StepConfig) -> list[str]:
        errors: list[str] = []
        if not step.get_domain_field("pipeline_id"):
            errors.append(f"Step '{step.id}' missing required 'pipeline_id' field")
        return errors
