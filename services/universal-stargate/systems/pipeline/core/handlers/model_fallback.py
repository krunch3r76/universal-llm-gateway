"""Model fallback resolution for generate steps.

When the primary model (from model_ref / models.yaml) raises ProxyClientError,
resolve model_requirements via the intelligence profile store to find ranked
alternatives and try them in order.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from universal_logging import get_logger

from ..execution.fallback_eligibility import get_fallback_suppression_reason

if TYPE_CHECKING:
    from ..schemas import PromptConfig, StepConfig
    from ..step_config import ResolvedTargetModel
    from .protocol import PipelineContext, StepOutput

logger = get_logger(__name__)


async def resolve_fallback_models(
    step: StepConfig,
    context: PipelineContext,
    *,
    exclude: str,
    primary_resolution: ResolvedTargetModel | None,
) -> list[str]:
    """Resolve model_requirements to a ranked fallback list, excluding primary."""
    from ..execution.resolved_candidates import get_ranked_candidates

    suppression_reason = get_fallback_suppression_reason(
        primary_resolution=primary_resolution,
        model_requirements=step.model_requirements,
    )
    if suppression_reason:
        logger.warning(
            "[%s] Suppressing fallback candidate resolution for '%s': %s",
            step.name,
            exclude,
            suppression_reason,
        )
        return []

    model_ids = await get_ranked_candidates(
        context=context,
        step_name=step.name,
        requirements=dict(step.model_requirements or {}),
    )
    return [m for m in model_ids if m != exclude]


async def try_fallbacks(
    handler: Any,
    step: StepConfig,
    context: PipelineContext,
    prompt_config: PromptConfig,
    user_prompt: str,
    source_provenance: dict[str, Any] | None,
    fallback_ids: list[str],
    *,
    primary_model: str,
    primary_error: str,
    last_error: Exception,
) -> StepOutput:
    """Try fallback models in order. Raises last error if all fail."""
    from ..events.inference import ModelFallbackResolved
    from ..execution.proxy_client import ProxyClientError

    for idx, fallback_id in enumerate(fallback_ids):
        logger.info(
            "[%s] Fallback attempt %d/%d: %s",
            step.name,
            idx + 1,
            len(fallback_ids),
            fallback_id,
        )
        try:
            result = await handler._invoke_model(
                step,
                context,
                prompt_config,
                fallback_id,
                user_prompt,
                source_provenance,
            )
            if context.recorder:
                context.recorder.emit(
                    ModelFallbackResolved(
                        step_name=step.name,
                        model_id=fallback_id,
                        primary_model=primary_model,
                        fallback_model=fallback_id,
                        primary_error=primary_error,
                        fallback_attempt=idx + 1,
                    )
                )
            return result
        except ProxyClientError as e:
            logger.warning(
                "[%s] Fallback model '%s' also failed: %s",
                step.name,
                fallback_id,
                e,
            )
            last_error = e

    raise last_error
