"""Step-level model fallback for the DAG executor.

When the primary model's full retry chain fails (timeout, proxy error, handler
error), resolves model_requirements via the intelligence profile store to find
ranked alternatives and re-runs the wrapper chain for each.

Each fallback model gets its own fresh retry+timeout allocation.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any

from universal_logging import get_logger

from ..fallback_eligibility import classify_fallback_error
from ..resolved_candidates import get_ranked_candidates

if TYPE_CHECKING:
    from ...handlers.protocol import PipelineContext, StepOutput
    from ...step_config import StepConfig

logger = get_logger(__name__)


async def try_step_model_fallback(
    step: StepConfig,
    primary_err: Exception,
    *,
    primary_model_id: str | None,
    run_step_fn: Callable[[StepConfig], Awaitable[StepOutput]],
    context: PipelineContext,
    get_event_context: Callable[[], tuple[str, str]],
    publish_event: Callable[..., Any],
) -> StepOutput:
    """Try fallback models after the primary model's retry chain fails.

    Receives the already-resolved primary model from the executor/coordinator
    to avoid independent re-resolution that could silently diverge from the
    coordinated model identity used for gating and eviction protection.

    Args:
        step: Step config (must have model_ref and model_requirements).
        primary_err: The exception from the primary model's retry chain.
        primary_model_id: The resolved primary model ID from the coordinator.
            If unavailable, fallback is skipped and primary_err is re-raised.
        run_step_fn: Callable that re-runs the step through the wrapper chain.
        context: PipelineContext for model resolution and event recording.
        get_event_context: Returns (pipeline_id, execution_id).
        publish_event: Fire-and-forget bus event publisher.

    Returns:
        StepOutput from the first successful fallback model.

    Raises:
        The last exception if all fallback models fail.
    """
    from ...events.inference import (
        StepModelFallbackAttempted,
        StepModelFallbackExhausted,
        StepModelFallbackSucceeded,
        StepModelFallbackSuppressed,
    )
    from ...events.step import StepModelFallback

    if not primary_model_id:
        raise primary_err
    primary_model = primary_model_id

    if not step.model_requirements:
        raise primary_err

    fallback_ids = [
        m
        for m in await get_ranked_candidates(
            context=context,
            step_name=step.name,
            requirements=dict(step.model_requirements or {}),
        )
        if m != primary_model
    ]

    if not fallback_ids:
        logger.info(
            "[%s] No fallback models for requirements %s (primary=%s)",
            step.name,
            step.model_requirements,
            primary_model,
        )
        raise primary_err

    primary_error_type = type(primary_err).__name__
    logger.warning(
        "[%s] Primary model '%s' failed (%s: %s), trying %d fallback model(s)",
        step.name,
        primary_model,
        primary_error_type,
        primary_err,
        len(fallback_ids),
    )

    recorder = context.recorder
    pipeline_id, execution_id = get_event_context()
    last_error: Exception = primary_err

    for idx, fallback_id in enumerate(fallback_ids, 1):
        if recorder:
            recorder.emit(
                StepModelFallbackAttempted(
                    step_name=step.name,
                    model_id=fallback_id,
                    primary_model=primary_model,
                    fallback_model=fallback_id,
                    primary_error=str(primary_err),
                    primary_error_type=primary_error_type,
                    fallback_attempt=idx,
                    total_fallbacks=len(fallback_ids),
                )
            )

        logger.info(
            "[%s] Fallback attempt %d/%d: %s",
            step.name,
            idx,
            len(fallback_ids),
            fallback_id,
        )

        context._step_model_override[step.name] = fallback_id
        try:
            result = await run_step_fn(step)

            if recorder:
                recorder.emit(
                    StepModelFallbackSucceeded(
                        step_name=step.name,
                        model_id=fallback_id,
                        primary_model=primary_model,
                        fallback_model=fallback_id,
                        primary_error=str(primary_err),
                        fallback_attempt=idx,
                    )
                )
            publish_event(
                StepModelFallback(
                    pipeline_id=pipeline_id,
                    execution_id=execution_id,
                    step_name=step.name,
                    primary_model=primary_model,
                    fallback_model=fallback_id,
                    primary_error_type=primary_error_type,
                    fallback_attempt=idx,
                    total_fallbacks=len(fallback_ids),
                    succeeded=True,
                )
            )
            return result

        except Exception as e:
            eligibility = classify_fallback_error(e)
            if not eligibility.should_fallback:
                if recorder:
                    recorder.emit(
                        StepModelFallbackSuppressed(
                            step_name=step.name,
                            model_id=fallback_id,
                            primary_error_type=eligibility.error_type,
                            suppression_reason=eligibility.reason,
                        )
                    )
                publish_event(
                    StepModelFallbackSuppressed(
                        pipeline_id=pipeline_id,
                        execution_id=execution_id,
                        step_name=step.name,
                        primary_error_type=eligibility.error_type,
                        suppression_reason=eligibility.reason,
                    )
                )
                raise
            logger.warning(
                "[%s] Fallback model '%s' failed: %s: %s",
                step.name,
                fallback_id,
                type(e).__name__,
                e,
            )
            publish_event(
                StepModelFallback(
                    pipeline_id=pipeline_id,
                    execution_id=execution_id,
                    step_name=step.name,
                    primary_model=primary_model,
                    fallback_model=fallback_id,
                    primary_error_type=primary_error_type,
                    fallback_attempt=idx,
                    total_fallbacks=len(fallback_ids),
                    succeeded=False,
                )
            )
            last_error = e
        finally:
            context._step_model_override.pop(step.name, None)

    logger.error(
        "[%s] All %d fallback models exhausted (primary=%s)",
        step.name,
        len(fallback_ids),
        primary_model,
    )
    if recorder:
        recorder.emit(
            StepModelFallbackExhausted(
                step_name=step.name,
                model_id=primary_model,
                primary_model=primary_model,
                fallback_models_tried=fallback_ids,
                primary_error=str(primary_err),
                final_error=str(last_error),
            )
        )

    raise last_error
