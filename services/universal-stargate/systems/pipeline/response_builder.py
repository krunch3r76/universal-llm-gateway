"""
Response builder for pipeline results.
"""

import json
import time
import uuid
from typing import TYPE_CHECKING, Any

from fastapi.responses import Response
from universal_logging import get_logger

from .core.execution.map_reduce import MapOutputCollection
from .core.handlers.protocol import StepOutput
from .schemas import PipelineSpec

if TYPE_CHECKING:
    from .core.handlers.protocol import PipelineContext

logger = get_logger(__name__)


def _collect_resolved_models(
    pipeline_context: "PipelineContext",
    execution_order: list[str] | None,
) -> list[str]:
    """Collect resolved model_id from each step output, in execution order."""
    order = (
        execution_order
        if execution_order is not None
        else list(pipeline_context.outputs.keys())
    )
    result: list[str] = []
    for step_id in order:
        out = pipeline_context.outputs.get(step_id)
        if out is None:
            continue
        if isinstance(out, MapOutputCollection):
            for inner in out.all_outputs():
                if getattr(inner, "model_id", None):
                    result.append(inner.model_id)
            continue
        if isinstance(out, StepOutput) and out.model_id:
            result.append(out.model_id)
    return result


class ResponseBuilder:
    """Build OpenAI-compatible responses for pipeline executions."""

    @staticmethod
    def build_response(
        pipeline_context: "PipelineContext",
        final_result: str,
        pipeline: PipelineSpec,
        step_outputs: dict[str, str],
        backtranslation: dict[str, Any] | None,
        execution_order: list[str] | None = None,
    ) -> Response:
        """
        Build strictly OpenAI-compliant response from pipeline execution result.

        Args:
            pipeline_context: Execution context with source text and outputs
            final_result: Final pipeline output text
            pipeline: Pipeline specification with options
            step_outputs: Map of step_id -> output text for alternates
            backtranslation: Optional backtranslation data
            execution_order: Optional step IDs in execution order (for step_stats)

        Returns:
            FastAPI Response with JSON body containing OpenAI chat completion format.

            Standard fields (always present):
            - choices[0].message: Assistant message only (OpenAI spec)
            - usage: Token counts aggregated from all pipeline steps
            - resolved_models: List of resolved model IDs in step order (non-standard)

            Optional extensions (non-standard, only if enabled):
            - pipeline.alternates: All step outputs (for A/B testing)
            - pipeline.backtranslation: Backtranslation data (if step present)
            - pipeline.step_stats: Per-step token and latency breakdown (if enabled)

        Note:
            Clients maintain conversation history as with standard OpenAI API.
            No conversation history is returned in the response.
        """
        def _aggregate_tokens(output_obj: Any) -> tuple[int, int]:
            if isinstance(output_obj, MapOutputCollection):
                prompt = sum(inner.prompt_tokens for inner in output_obj.all_outputs())
                completion = sum(
                    inner.completion_tokens for inner in output_obj.all_outputs()
                )
                return prompt, completion
            if isinstance(output_obj, StepOutput):
                return output_obj.prompt_tokens, output_obj.completion_tokens
            return 0, 0

        # Aggregate token usage from all steps (handles MapOutputCollection)
        prompt_tokens = 0
        completion_tokens = 0
        for out in pipeline_context.outputs.values():
            p, c = _aggregate_tokens(out)
            prompt_tokens += p
            completion_tokens += c
        total_tokens = prompt_tokens + completion_tokens

        # Build OpenAI-compliant response body
        body: dict[str, Any] = {
            "id": f"chatcmpl-pipeline-{uuid.uuid4().hex[:12]}",
            "object": "chat.completion",
            "created": int(time.time()),
            "model": pipeline.id,
            "choices": [
                {
                    "index": 0,
                    "message": {
                        "role": "assistant",
                        "content": final_result,
                    },
                    "finish_reason": "stop",
                }
            ],
            "usage": {
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "total_tokens": total_tokens,
            },
            # Non-standard: resolved model IDs per step
            "resolved_models": _collect_resolved_models(
                pipeline_context, execution_order
            ),
        }

        # Optional: Stargate-derived timing for RAG (post-queue processing time)
        for out in pipeline_context.outputs.values():
            if isinstance(out, MapOutputCollection):
                ps = getattr(out, "processing_seconds", None)
                if ps is not None and isinstance(ps, int | float):
                    body["pipeline_timing"] = {
                        "processing_seconds": round(ps, 3),
                        "queue_wait_seconds": round(
                            getattr(out, "queue_wait_seconds", 0) or 0, 3
                        ),
                    }
                    break

        # Optional: Include alternates in non-standard extension
        # (only if explicitly enabled and client expects it)
        if pipeline.options.include_alternates:
            body["pipeline"] = {
                "alternates": [{"id": k, "content": v} for k, v in step_outputs.items()]
            }

        # Optional: Include backtranslation data if present
        if backtranslation:
            body.setdefault("pipeline", {})["backtranslation"] = backtranslation

        # Optional: per-step token breakdown (order by execution_order when provided)
        include_step_stats = pipeline.options.get("include_step_stats", False)
        if include_step_stats:
            def _build_step_stats(
                step_id: str, output_obj: Any
            ) -> dict[str, Any] | None:
                if isinstance(output_obj, MapOutputCollection):
                    inners = output_obj.all_outputs()
                    map_prompt = sum(o.prompt_tokens for o in inners)
                    map_comp = sum(o.completion_tokens for o in inners)
                    map_calls = sum(getattr(o, "model_call_count", 0) for o in inners)
                    latencies = [o.latency_ms for o in inners]
                    first_model = next(
                        (o.model_id for o in inners if getattr(o, "model_id", None)),
                        None,
                    )
                    return {
                        "step": step_id,
                        "prompt_tokens": map_prompt,
                        "completion_tokens": map_comp,
                        "total_tokens": map_prompt + map_comp,
                        "latency_ms": round(max(latencies, default=0.0), 1),
                        "model_calls": map_calls or len(inners),
                        **({"model_id": first_model} if first_model else {}),
                    }
                if isinstance(output_obj, StepOutput):
                    step_total = output_obj.prompt_tokens + output_obj.completion_tokens
                    return {
                        "step": step_id,
                        "prompt_tokens": output_obj.prompt_tokens,
                        "completion_tokens": output_obj.completion_tokens,
                        "total_tokens": step_total,
                        "latency_ms": round(output_obj.latency_ms, 1),
                        "model_calls": getattr(output_obj, "model_call_count", 0),
                        **(
                            {"model_id": output_obj.model_id}
                            if output_obj.model_id
                            else {}
                        ),
                    }
                return None

            step_stats: list[dict[str, Any]] = []
            order = (
                execution_order if execution_order else list(pipeline_context.outputs)
            )
            for step_id in order:
                out = pipeline_context.outputs.get(step_id)
                if out is None:
                    continue
                stats = _build_step_stats(step_id, out)
                if stats is not None:
                    step_stats.append(stats)
            if "pipeline" not in body:
                body["pipeline"] = {}
            body["pipeline"]["step_stats"] = step_stats

        return Response(
            content=json.dumps(body),
            media_type="application/json",
            status_code=200,
            headers={
                "X-Pipeline-Execution-Id": pipeline_context.execution_id,
            },
        )
