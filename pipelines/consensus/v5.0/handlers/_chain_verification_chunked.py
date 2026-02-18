"""
Chunked batch verification for chain enrichment.

Parses batch JSON responses and executes verification via ChunkedModelExecutor.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from systems.pipeline.core.execution.chunked import (
    ChunkedModelExecutor,
    FirstAvailable,
    ModelExecutionConfig,
    ProcessResult,
    SkipFallback,
    create_chunk_strategy,
)
from systems.pipeline.core.execution.chunked.chunk_types import Chunk
from universal_logging import get_logger

from ._chain_utils import strip_json_fences, token_budget
from ._verdict import normalize_verdict
from .v4_types import VerdictEntry, VerificationChunkTiming, VerificationModelTiming

if TYPE_CHECKING:
    from systems.pipeline.core.handlers.builtin import BaseHandler
    from systems.pipeline.core.handlers.protocol import PipelineContext
    from systems.pipeline.core.schemas import StepConfig

logger = get_logger(__name__)
execution_logger = get_logger("systems.pipeline.execution")


def parse_batch_verdicts_chain(
    content: str,
    step_id: str,
    expected_count: int,
    chunk_context: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Parse batch verify JSON evaluations array into list of verdict dicts.

    Handles two cases:
    1. Standard: {"evaluations": [...]}
    2. Single-claim fallback: {"verdict": ..., "reasoning": ...}
       (some models return bare object for 1 claim instead of array)

    Args:
        content: Raw model response
        step_id: Step ID for error messages
        expected_count: Expected number of evaluations
        chunk_context: Optional context for tracing (execution_id, question, chunk_index, claims)
    """

    def _truncate_for_log(text: str, max_len: int = 1000) -> str:
        """Truncate text for logging, preserving structure."""
        if len(text) <= max_len:
            return text
        return text[:max_len] + f"... (truncated, total {len(text)} chars)"

    def _format_trace_context() -> str:
        """Format tracing context for error logs."""
        if not chunk_context:
            return ""
        parts = []
        if "execution_id" in chunk_context:
            parts.append(f"execution_id={chunk_context['execution_id']}")
        if "chunk_index" in chunk_context:
            parts.append(f"chunk={chunk_context['chunk_index']}")
        if "question" in chunk_context:
            q = chunk_context["question"]
            parts.append(f"question='{q[:80]}{'...' if len(q) > 80 else ''}'")
        return "\n" + "\n".join(parts) if parts else ""

    def _format_chunk_claims() -> str:
        """Format the claims in this chunk for debugging."""
        if not chunk_context or "claims" not in chunk_context:
            return ""
        claims = chunk_context["claims"]
        claim_texts = [
            f"  [{i + 1}] {c.get('text', '')[:100]}" for i, c in enumerate(claims)
        ]
        return "\nClaims in chunk:\n" + "\n".join(claim_texts)

    trace_ctx = _format_trace_context()
    claims_ctx = _format_chunk_claims()

    try:
        parsed = json.loads(strip_json_fences(content))
    except json.JSONDecodeError as e:
        logger.error(
            "Chain verify batch: JSON parse failed: %s%s%s\nRaw content: %s",
            e,
            trace_ctx,
            claims_ctx,
            _truncate_for_log(content),
        )
        execution_logger.error(
            "Chain verify batch: JSON parse failed: %s%s%s\nFull raw content:\n%s",
            e,
            trace_ctx,
            claims_ctx,
            content,
        )
        e.add_note(f"Full model response:\n{content}")
        raise
    if not isinstance(parsed, dict):
        error_msg = (
            f"Step '{step_id}': batch verify expected object, "
            f"got {type(parsed).__name__}"
        )
        logger.error(
            "%s%s%s\nReceived: %s",
            error_msg,
            trace_ctx,
            claims_ctx,
            _truncate_for_log(str(parsed)),
        )
        full_response = str(parsed)
        execution_logger.error(
            "%s%s%s\nFull received content:\n%s",
            error_msg,
            trace_ctx,
            claims_ctx,
            full_response,
        )
        exc = ValueError(error_msg)
        exc.add_note(f"Full model response:\n{content}")
        raise exc

    evaluations = parsed.get("evaluations")
    if not isinstance(evaluations, list):
        if expected_count == 1 and "verdict" in parsed:
            logger.warning(
                "Chain verify batch: model returned bare verdict object for single claim, wrapping in array"
            )
            evaluations = [parsed]
        else:
            error_msg = (
                f"Step '{step_id}': batch verify missing or invalid 'evaluations' array"
            )
            logger.error(
                "%s%s%s\nReceived keys: %s\nFull response: %s",
                error_msg,
                trace_ctx,
                claims_ctx,
                list(parsed.keys()),
                _truncate_for_log(json.dumps(parsed, indent=2)),
            )
            full_response = json.dumps(parsed, indent=2)
            execution_logger.error(
                "%s%s%s\nReceived keys: %s\nFull response:\n%s",
                error_msg,
                trace_ctx,
                claims_ctx,
                list(parsed.keys()),
                full_response,
            )
            exc = ValueError(error_msg)
            exc.add_note(f"Full model response:\n{content}")
            raise exc

    # Allow extra evaluations (model commentary), but require at least expected_count
    if len(evaluations) < expected_count:
        error_msg = (
            f"Step '{step_id}': batch verify returned {len(evaluations)} evaluations, "
            f"expected {expected_count} (too few)"
        )
        logger.error(
            "%s%s%s\nReceived evaluations: %s",
            error_msg,
            trace_ctx,
            claims_ctx,
            _truncate_for_log(json.dumps(evaluations, indent=2)),
        )
        full_response = json.dumps(evaluations, indent=2)
        execution_logger.error(
            "%s%s%s\nFull received evaluations:\n%s",
            error_msg,
            trace_ctx,
            claims_ctx,
            full_response,
        )
        exc = ValueError(error_msg)
        exc.add_note(f"Full model response:\n{content}")
        raise exc

    if len(evaluations) > expected_count:
        logger.warning(
            "Step '%s': batch verify returned %d evaluations, expected %d (using first %d with matching indices)%s",
            step_id,
            len(evaluations),
            expected_count,
            expected_count,
            trace_ctx,
        )
        # Build index→evaluation lookup, prefer explicit index field
        eval_by_idx: dict[int, dict[str, Any]] = {}
        for i, ev in enumerate(evaluations):
            # Use explicit index field if present, else position
            idx = ev.get("index", i)
            if isinstance(idx, int) and 0 <= idx < expected_count:
                eval_by_idx[idx] = ev
        # Take first expected_count by index
        evaluations = [
            eval_by_idx.get(i, evaluations[i]) for i in range(expected_count)
        ]

    # Normalize: some models return reasoning as nested object instead of string
    for ev in evaluations:
        reasoning = ev.get("reasoning")
        if reasoning is not None and not isinstance(reasoning, str):
            ev["reasoning"] = json.dumps(reasoning, ensure_ascii=False)

    return evaluations


async def verify_batch_chunked(
    handler: BaseHandler,
    eligible: list[dict[str, Any]],
    question: str,
    model_id: str,
    step: StepConfig,
    context: PipelineContext,
    gen_params: dict[str, Any],
    exec_config: ModelExecutionConfig,
    prompt_ref_batch: str,
) -> tuple[dict[str, VerdictEntry], VerificationModelTiming]:
    """Verify eligible claims in chunks via ChunkedModelExecutor."""
    executor = ChunkedModelExecutor(
        model_selector=FirstAvailable([model_id]),
        chunk_strategy=create_chunk_strategy(exec_config),
        fallback_handler=SkipFallback(),
        max_concurrent=exec_config.max_concurrent,
        timeout_per_chunk_ms=exec_config.timeout_ms,
    )

    async def process_chunk(
        chunk: Chunk, assigned_model_id: str
    ) -> ProcessResult | list[Any]:
        lines: list[str] = []
        for i, it in enumerate(chunk.items):
            line = f"[{i}] {it.get('text', '')}"
            parent = it.get("parent_text", "")
            if parent:
                line += f"\n    [PARENT CLAIM: {parent}]"
            lines.append(line)
        numbered_claims = "\n".join(lines)
        template_ctx = {
            "claim_count": str(len(chunk.items)),
            "cleaned_question": question,
            "numbered_claims": numbered_claims,
        }
        rendered = handler._render_prompt(
            prompt_ref_batch,
            template_ctx,
            context,
            safe=True,
        )
        call_result = await handler._call_model(
            assigned_model_id,
            rendered.user_prompt,
            step,
            context,
            system_prompt=rendered.system_prompt,
            temperature=gen_params.get("temperature", 0.0),
            max_tokens=handler._constrained_tokens(
                token_budget(context, "verify_batch", 1024), context
            ),
            call_label="verify_batch",
            json_schema={
                "type": "object",
                "properties": {
                    "evaluations": {
                        "type": "array",
                        # Grammar-level enforcement: model cannot close array early
                        "minItems": len(chunk.items),
                        "maxItems": len(chunk.items),
                        "items": {
                            "type": "object",
                            "properties": {
                                "index": {"type": "integer"},
                                "verdict": {"type": "boolean"},
                                "reasoning": {"type": "string"},
                            },
                            "required": ["index", "verdict", "reasoning"],
                        },
                    }
                },
                "required": ["evaluations"],
            },
        )

        if call_result.finish_reason == "length":
            logger.warning(
                "Chain verify batch: model '%s' stopped due to length limit on chunk %d "
                "(tokens: %d prompt + %d completion, claims: %d). Response may be incomplete. "
                "Consider increasing verify_batch token budget.",
                assigned_model_id,
                chunk.index,
                call_result.prompt_tokens,
                call_result.completion_tokens,
                len(chunk.items),
            )

        chunk_context = {
            "execution_id": context.execution_id,
            "chunk_index": chunk.index,
            "question": question,
            "claims": chunk.items,
        }
        rid = call_result.snapshot_request_id
        try:
            evaluations = parse_batch_verdicts_chain(
                call_result.content, step.id, len(chunk.items), chunk_context
            )
        except Exception as e:
            # Attach request_id so ChunkedModelExecutor can propagate it
            e.request_id = rid  # type: ignore[attr-defined]
            raise
        results_list: list[dict[str, Any]] = []
        for item, eval_data in zip(chunk.items, evaluations, strict=True):
            verdict = normalize_verdict(eval_data.get("verdict"))
            reasoning = eval_data.get("reasoning", "")
            results_list.append(
                {
                    "statement_id": item.get("statement_id", ""),
                    "verdict": {"v": verdict, "r": reasoning},
                }
            )
        return ProcessResult(
            results=results_list,
            prompt_tokens=getattr(call_result, "prompt_tokens", 0) or 0,
            completion_tokens=getattr(call_result, "completion_tokens", 0) or 0,
            request_id=rid,
        )

    result = await executor.execute(eligible, process_chunk)

    chunk_timings = [
        VerificationChunkTiming(
            chunk_index=cr.chunk_index,
            num_items=len(cr.item_indices),
            latency_ms=cr.latency_ms,
            prompt_tokens=cr.prompt_tokens,
            completion_tokens=cr.completion_tokens,
        )
        for cr in result.chunk_results
    ]

    timing = VerificationModelTiming(
        model_id=model_id,
        num_claims=len(eligible),
        latency_ms=result.total_latency_ms,
        mode="chunked",
        chunk_size=exec_config.chunk_size,
        chunks=chunk_timings,
        prompt_tokens=result.total_prompt_tokens,
        completion_tokens=result.total_completion_tokens,
    )

    none_count = sum(1 for r in result.results if r is None)
    if none_count > 0:
        failed_chunks: list[dict[str, Any]] = []
        for chunk_result in result.chunk_results:
            if chunk_result.error:
                # item_indices points directly to the claims in this chunk
                chunk_claims = [
                    eligible[i] for i in chunk_result.item_indices if i < len(eligible)
                ]
                failed_chunks.append(
                    {
                        "chunk_index": chunk_result.chunk_index,
                        "model": chunk_result.model_used,
                        "error": chunk_result.error,
                        "claims": [c.get("text", "") for c in chunk_claims],
                        "request_id": chunk_result.request_id,
                    }
                )

        logger.error(
            "Chain verify batch: execution_id=%s, step=%s, question='%s'",
            context.execution_id,
            step.id,
            question[:100] + "..." if len(question) > 100 else question,
        )
        execution_logger.error(
            "Chain verify batch: execution_id=%s, step=%s, full_question='%s'",
            context.execution_id,
            step.id,
            question,
        )
        for fc in failed_chunks:
            claims_text = "\n    ".join(
                f"[{i}] {c}" for i, c in enumerate(fc["claims"])
            )
            logger.error(
                "Chain verify batch: chunk %d failed with model '%s': %s\n"
                "  Claims in chunk (%d):\n    %s",
                fc["chunk_index"],
                fc["model"],
                fc["error"],
                len(fc["claims"]),
                claims_text,
            )
            execution_logger.error(
                "Chain verify batch: chunk %d failed with model '%s': %s\n"
                "  Claims in chunk (%d):\n    %s",
                fc["chunk_index"],
                fc["model"],
                fc["error"],
                len(fc["claims"]),
                claims_text,
            )

        model_list = ", ".join(
            f"{fc['model']} (chunk {fc['chunk_index']})" for fc in failed_chunks
        )
        logger.error(
            "Chain verify batch: %d/%d chunks failed. Failed models: %s",
            len(failed_chunks),
            len(result.chunk_results),
            model_list,
        )
        execution_logger.error(
            "Chain verify batch: %d/%d chunks failed. Failed models: %s",
            len(failed_chunks),
            len(result.chunk_results),
            model_list,
        )

        exc = ValueError(
            f"Batch verification failed: {len(failed_chunks)}/{len(result.chunk_results)} chunks "
            f"returned invalid JSON. Failed models: {model_list}"
        )
        failed_rids = [fc["request_id"] for fc in failed_chunks if fc.get("request_id")]
        if failed_rids:
            exc.add_note(f"failed_request_ids: {','.join(failed_rids)}")
        raise exc

    return {r["statement_id"]: r["verdict"] for r in result.results}, timing
