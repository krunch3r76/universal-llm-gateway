"""
pipeline_call_v1 step handler (core builtin).

Calls another pipeline as a service via Stargate's chat completions endpoint.
Enables any pipeline to use service pipelines (e.g. rag-context) as a step
without duplicating logic inline.

Domain fields (from pipeline YAML step config):
    pipeline_id: str  — virtual model ID of the pipeline to call (required)
    pipeline_options: dict — optional static options for the sub-pipeline
    consumer_model_ref: str — model alias resolved at runtime via models.yaml;
        injected as ``consumer_model`` into pipeline_options so the callee
        can apply model-specific retrieval profiles (optional)
    stargate_url: str — Stargate base URL (default: http://localhost:9999)

Forwards rag_*, scope_*, and rerank_* keys from context.options so callers
can tune retrieval and reranking via the end-to-end path (e.g. rag-answer →
rag-context).
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any, override

import httpx
from universal_logging import get_logger

from ..dag import PipelineExecutionError
from .protocol import AbstractStepHandler, StepOutput
from .registry import register_handler

if TYPE_CHECKING:
    from ..schemas import StepConfig
    from .protocol import PipelineContext

logger = get_logger(__name__)

DEFAULT_STARGATE_URL = "http://localhost:9999"


def _inject_rag_context_options(merged_options: dict[str, Any], step_id: str) -> None:
    """Inject scope_options and corpus_hints for rag-context pipeline when absent."""
    if "scope_options" not in merged_options:
        try:
            from pipelines.rag.scope_helpers import fetch_scope_options_text

            merged_options["scope_options"] = fetch_scope_options_text()
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                (
                    "pipeline_call_v1 '%s': failed to inject scope_options "
                    "for rag-context (%s)"
                ),
                step_id,
                exc,
            )
    if "corpus_hints" not in merged_options:
        try:
            from pipelines.rag.corpus_hints_loader import fetch_corpus_hints_text

            merged_options["corpus_hints"] = fetch_corpus_hints_text()
        except Exception as exc:  # noqa: BLE001
            logger.debug(
                "pipeline_call_v1 '%s': could not load corpus hints: %s",
                step_id,
                exc,
            )


@register_handler
class PipelineCallHandler(AbstractStepHandler):
    """
    Call a pipeline by its virtual model ID via Stargate.

    Sends context.source_text as the user message and returns the
    pipeline's response as StepOutput.raw. Forwards pipeline_options
    from step config and rag_* / scope_* / rerank_* from context.options.

    If ``consumer_model_ref`` is set, resolves the alias via the
    calling pipeline's models.yaml and injects ``consumer_model``
    into pipeline_options for profile-aware retrieval.

    Invariants:
    - ∀ execute(): pipeline_id field is required
    - ∀ response: StepOutput.raw = pipeline response text or error message
    - ∀ empty/error response: returns meaningful message (§7.2)
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

        step_options: dict[str, Any] = step.get_domain_field("pipeline_options", {})
        forwarded: dict[str, Any] = {
            k: v
            for k, v in context.options.items()
            if k.startswith(("rag_", "scope_", "rerank_"))
        }
        merged_options = {**step_options, **forwarded}
        if pipeline_id == "rag-context":
            _inject_rag_context_options(merged_options, step.id)

        consumer_model_ref: str = step.get_domain_field("consumer_model_ref", "")
        if consumer_model_ref and context._registry is not None:
            try:
                model_config = context._registry.get_model_config(
                    consumer_model_ref,
                    domain=context.pipeline.domain,
                    search_path=context.pipeline.source_search_path,
                )
                merged_options["consumer_model"] = model_config.model
                logger.debug(
                    "pipeline_call_v1 '%s': resolved consumer_model_ref '%s' → '%s'",
                    step.id,
                    consumer_model_ref,
                    model_config.model,
                )
            except KeyError:
                logger.warning(
                    "pipeline_call_v1 '%s': consumer_model_ref '%s' not found "
                    "in model registry — skipping profile injection",
                    step.id,
                    consumer_model_ref,
                )

        # Override: if the caller specified pipeline_options.model (e.g. a cloud
        # model), use it as consumer_model so the retrieval sub-pipeline applies
        # the right profile. This overrides the static alias resolution above.
        caller_model_override: str = context.runtime_options.get("model", "")
        if caller_model_override:
            merged_options["consumer_model"] = caller_model_override
            logger.debug(
                "pipeline_call_v1 '%s': pipeline_options.model overrides "
                "consumer_model → '%s'",
                step.id,
                caller_model_override,
            )

        body: dict[str, Any] = {
            "model": pipeline_id,
            "messages": [{"role": "user", "content": context.source_text}],
            "stream": False,
        }
        if merged_options:
            body["pipeline_options"] = merged_options

        timeout = (step.handler_timeout_seconds or step.timeout_seconds or 60) + 10

        start = time.monotonic()
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(url, json=body)
        latency_ms = (time.monotonic() - start) * 1000

        if response.is_error:
            detail_message = response.text
            try:
                error_payload = response.json()
            except ValueError:
                error_payload = None
            if isinstance(error_payload, dict):
                nested = error_payload.get("detail", error_payload.get("error", {}))
                if isinstance(nested, dict) and nested.get("message"):
                    detail_message = str(nested["message"])
                elif isinstance(nested, str) and nested.strip():
                    detail_message = nested
            raise PipelineExecutionError(
                f"Sub-pipeline '{pipeline_id}' failed: {detail_message}"
            )

        data = response.json()
        content: str = (
            data.get("choices", [{}])[0].get("message", {}).get("content", "") or ""
        )

        if not content.strip():
            error_detail = data.get("error", {})
            if isinstance(error_detail, dict) and error_detail.get("message"):
                content = (
                    f"Pipeline '{pipeline_id}' returned an error: "
                    f"{error_detail['message']}"
                )
            else:
                content = (
                    f"Retrieval unavailable — pipeline '{pipeline_id}' returned "
                    "empty content. The answer is generated from model knowledge only."
                )

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
