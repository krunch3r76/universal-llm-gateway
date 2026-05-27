"""
Primary model resolution for the generate handler.

Encapsulates the 5-tier resolution chain that ``GenericGenerateHandler.execute``
runs before invoking a model:

1. Executor-level override (``context._step_model_override`` — DAGExecutor's
   step-level fallback for timeouts / arbitrary errors). Full ID; no
   ResolvedTargetModel is built so handler-level fallback is skipped.
2. Runtime override (``options['model_ref_overrides']`` — e.g. ``--models`` CLI
   flag). Built as ``resolution_source='model_ref_override'``.
3. Auto-select (``model_ref == 'auto'`` or absent + ``model_requirements`` →
   intelligence profile via ``get_ranked_candidates``). Merges any
   ``avoid_models_from``-resolved exclusions into the requirements.
4. Registry lookup (``model_ref`` → ``models.yaml`` via
   ``registry.get_model_config``). Returns the registered profile.
5. Raw model ID passthrough (``model_ref`` not in ``models.yaml``).

Returns ``(model_id, model_profile, primary_resolution)``. The caller issues
a single ``_invoke_model`` against the returned tuple and, on
``ProxyClientError``, runs ``get_fallback_suppression_reason`` +
``resolve_fallback_models`` against the captured ``ResolvedTargetModel``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from universal_logging import get_logger

from ...step_config import ResolvedTargetModel
from ...step_config.model_resolution import get_requirements_source
from .avoid_models import _resolve_avoid_models

if TYPE_CHECKING:
    from ...schemas import StepConfig
    from ..protocol import PipelineContext

logger = get_logger(__name__)


async def resolve_primary_model(
    step: StepConfig,
    context: PipelineContext,
) -> tuple[str, str | None, ResolvedTargetModel | None]:
    """Resolve ``(model_id, model_profile, primary_resolution)`` for the step.

    Implements the 5-tier chain documented at the module level. Logs the
    same informational lines (runtime override, auto-select, raw model_ref)
    that the original inline branches in ``execute`` emitted, so log-driven
    diagnostics are unchanged.
    """
    registry = context._registry

    executor_override = context._step_model_override.get(step.name)
    _raw_overrides = context.options.get("model_ref_overrides")
    model_ref_overrides: dict[str, Any] = (
        _raw_overrides if isinstance(_raw_overrides, dict) else {}
    )
    runtime_override = (
        model_ref_overrides.get(step.name) or model_ref_overrides.get(step.model_ref)
        if step.model_ref
        else None
    )
    if runtime_override:
        runtime_override = runtime_override.strip()
    elif model_ref_overrides and step.model_ref:
        override_keys = (
            list(model_ref_overrides.keys())
            if isinstance(model_ref_overrides, dict)
            else "n/a"
        )
        logger.debug(
            "[%s] model_ref_overrides present but no match for "
            "step.name=%r or step.model_ref=%r; keys=%s",
            step.name,
            step.name,
            step.model_ref,
            override_keys,
        )

    if executor_override:
        return executor_override, None, None

    if runtime_override:
        primary_resolution = ResolvedTargetModel.build(
            runtime_override,
            resolution_source="model_ref_override",
            model_ref=step.model_ref,
            requirements_source=get_requirements_source(step),
        )
        logger.info(
            "[%s] Using runtime model override: %s",
            step.name,
            runtime_override,
        )
        return runtime_override, None, primary_resolution

    if step.model_ref == "auto" or (not step.model_ref and step.model_requirements):
        from ...execution.resolved_candidates import get_ranked_candidates
        from ...execution.resolver import NamespaceResolver

        requirements = dict(step.model_requirements or {})
        if step.avoid_models_from:
            try:
                resolver = NamespaceResolver(context)
                avoided = _resolve_avoid_models(
                    step.avoid_models_from,
                    resolver,
                    step.name,
                )
                if avoided:
                    existing = requirements.get("avoid_models")
                    if isinstance(existing, list):
                        merged = [str(item) for item in existing if item]
                    elif isinstance(existing, str) and existing:
                        merged = [existing]
                    else:
                        merged = []
                    deduped = list(dict.fromkeys(merged + avoided))
                    requirements["avoid_models"] = deduped
            except (KeyError, AttributeError, ValueError) as exc:
                logger.warning(
                    "[%s] avoid_models_from resolution failed for '%s': %s. "
                    "Proceeding without model exclusion.",
                    step.name,
                    step.avoid_models_from,
                    exc,
                )

        candidates = await get_ranked_candidates(
            context=context,
            step_name=step.name,
            requirements=requirements,
        )
        if not candidates:
            logger.warning(
                "[%s] Auto model resolution returned no candidates for requirements=%s",
                step.name,
                requirements,
            )
            raise ValueError(
                f"Step '{step.name}': auto model resolution found no candidates "
                f"for requirements {requirements}. "
                f"Check that models matching the requirements are available "
                f"(source/task/min_score filters may be too restrictive, or "
                f"the /v1/models/select endpoint may be temporarily unavailable)."
            )
        model_id = candidates[0]
        primary_resolution = ResolvedTargetModel.build(
            model_id,
            resolution_source="model_requirements",
            model_ref=step.model_ref,
            requirements_source=get_requirements_source(step),
        )
        logger.info(
            "[%s] Auto-resolved model from requirements: %s",
            step.name,
            model_id,
        )
        return model_id, None, primary_resolution

    try:
        model_config = registry.get_model_config(
            step.model_ref,
            domain=context.pipeline.domain,
            search_path=context.pipeline.source_search_path,
        )
        model_id = model_config.model
        model_profile = model_config.profile
        primary_resolution = ResolvedTargetModel.build(
            model_id,
            resolution_source="registry_model_ref",
            model_ref=step.model_ref,
            requirements_source=get_requirements_source(step),
        )
        return model_id, model_profile, primary_resolution
    except KeyError:
        model_id = step.model_ref
        primary_resolution = ResolvedTargetModel.build(
            model_id,
            resolution_source="raw_model_ref",
            model_ref=step.model_ref,
            requirements_source=get_requirements_source(step),
        )
        logger.info(
            "[%s] Using raw model ID (not in models.yaml): %s",
            step.name,
            model_id,
        )
        return model_id, None, primary_resolution
