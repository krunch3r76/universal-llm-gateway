"""Sync + async target-model resolution helpers for :class:`StepConfig`.

The resolution order is contractual (coordination, fallback eligibility, and
the generate handler all depend on it being identical across sync and async
paths):

1. Pipeline runtime override — when handler semantics honor a runtime ``model``
   option (currently ``answer_v1`` + ``generate`` steps).
2. ``model_ref_overrides`` keyed by step name or by ``model_ref`` — explicit
   user/caller choice.
3. ``model_ref == "auto"`` or no ``model_ref`` but ``model_requirements`` set →
   ``/v1/models/select`` first candidate (via the requirements resolver).
4. ``model_ref`` → ``models.yaml`` registry lookup.
5. ``None`` when no ``model_ref`` is set and no ``model_requirements`` apply.

Helpers take a :class:`StepConfig` as their first positional argument so the
bound methods on the class can stay thin delegators. The async variant must
yield through ``/v1/models/select`` because the same Stargate process serves
that endpoint; the sync variant resolves candidates inline.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from universal_logging import get_logger

from .resolved_target_model import ResolvedTargetModel

if TYPE_CHECKING:
    from .config import StepConfig

logger = get_logger(__name__)


def get_pipeline_model_override(step: StepConfig, context: Any | None) -> str | None:
    """Return runtime model override when execution semantics honor it.

    ``answer_v1`` routes generate steps through ``pipeline_options.model`` when
    supplied. Coordination and fallback must resolve the same target model as
    handler execution so gating, eviction protection, and queueing apply to
    the actually requested model rather than the static ``model_ref`` alias.
    """
    if context is None:
        return None
    if getattr(context.pipeline, "domain", None) != "answer_v1":
        return None
    if step.type != "generate":
        return None

    options = getattr(context, "options", None)
    if not isinstance(options, dict):
        return None

    override = options.get("model")
    if isinstance(override, str) and override.strip():
        return override.strip()
    return None


def get_requirements_source(step: StepConfig) -> str | None:
    """Return the configured ``model_requirements.source`` value when present."""
    requirements = step.model_requirements
    if not isinstance(requirements, dict):
        return None
    source = requirements.get("source")
    return source if isinstance(source, str) and source else None


def resolve_target_model_sync(
    step: StepConfig,
    registry: Any,
    *,
    domain: str | None = None,
    search_path: str | None = None,
    model_ref_overrides: dict[str, str] | None = None,
    context: Any | None = None,
) -> ResolvedTargetModel | None:
    """Return structured target-model resolution metadata for ``step``."""
    runtime_override = get_pipeline_model_override(step, context)
    if runtime_override:
        return ResolvedTargetModel.build(
            runtime_override,
            resolution_source="pipeline_runtime_override",
            model_ref=step.model_ref,
            requirements_source=get_requirements_source(step),
        )

    if model_ref_overrides and step.model_ref:
        override = model_ref_overrides.get(step.name) or model_ref_overrides.get(
            step.model_ref
        )
        if isinstance(override, str) and override.strip():
            return ResolvedTargetModel.build(
                override.strip(),
                resolution_source="model_ref_override",
                model_ref=step.model_ref,
                requirements_source=get_requirements_source(step),
            )

    if step.model_ref == "auto" or (not step.model_ref and step.model_requirements):
        from ..execution.requirements_resolver import resolve_model_requirements

        candidates = resolve_model_requirements(step.model_requirements or {})
        if not candidates:
            return None
        return ResolvedTargetModel.build(
            candidates[0],
            resolution_source="model_requirements",
            model_ref=step.model_ref,
            requirements_source=get_requirements_source(step),
        )

    if not step.model_ref:
        return None
    step.validate_model_ref()

    try:
        model_config = registry.get_model_config(
            step.model_ref, domain=domain, search_path=search_path
        )
        if not model_config:
            return None
        return ResolvedTargetModel.build(
            model_config.model,
            resolution_source="registry_model_ref",
            model_ref=step.model_ref,
            requirements_source=get_requirements_source(step),
        )
    except KeyError:
        return ResolvedTargetModel.build(
            step.model_ref,
            resolution_source="raw_model_ref",
            model_ref=step.model_ref,
            requirements_source=get_requirements_source(step),
        )
    except Exception as exc:
        logger.warning(
            "Step '%s': model lookup failed for model_ref=%r: %s",
            step.name,
            step.model_ref,
            exc,
        )
        return None


async def resolve_target_model_async(
    step: StepConfig,
    registry: Any,
    *,
    domain: str | None = None,
    search_path: str | None = None,
    model_ref_overrides: dict[str, str] | None = None,
    context: Any | None = None,
) -> ResolvedTargetModel | None:
    """Async structured target-model resolution for live execution paths."""
    runtime_override = get_pipeline_model_override(step, context)
    if runtime_override:
        return ResolvedTargetModel.build(
            runtime_override,
            resolution_source="pipeline_runtime_override",
            model_ref=step.model_ref,
            requirements_source=get_requirements_source(step),
        )

    if model_ref_overrides and step.model_ref:
        override = model_ref_overrides.get(step.name) or model_ref_overrides.get(
            step.model_ref
        )
        if isinstance(override, str) and override.strip():
            return ResolvedTargetModel.build(
                override.strip(),
                resolution_source="model_ref_override",
                model_ref=step.model_ref,
                requirements_source=get_requirements_source(step),
            )

    if step.model_ref == "auto" or (not step.model_ref and step.model_requirements):
        from ..execution.requirements_resolver import (
            async_resolve_model_requirements,
        )
        from ..execution.resolved_candidates import get_ranked_candidates

        requirements = dict(step.model_requirements or {})
        if context is None:
            candidates = await async_resolve_model_requirements(requirements)
        else:
            candidates = await get_ranked_candidates(
                context=context,
                step_name=step.name,
                requirements=requirements,
            )
        if not candidates:
            return None
        return ResolvedTargetModel.build(
            candidates[0],
            resolution_source="model_requirements",
            model_ref=step.model_ref,
            requirements_source=get_requirements_source(step),
        )

    if not step.model_ref:
        return None
    step.validate_model_ref()

    try:
        model_config = registry.get_model_config(
            step.model_ref, domain=domain, search_path=search_path
        )
        if not model_config:
            return None
        return ResolvedTargetModel.build(
            model_config.model,
            resolution_source="registry_model_ref",
            model_ref=step.model_ref,
            requirements_source=get_requirements_source(step),
        )
    except KeyError:
        return ResolvedTargetModel.build(
            step.model_ref,
            resolution_source="raw_model_ref",
            model_ref=step.model_ref,
            requirements_source=get_requirements_source(step),
        )
    except Exception as exc:
        logger.warning(
            "Step '%s': async model lookup failed for model_ref=%r: %s",
            step.name,
            step.model_ref,
            exc,
        )
        return None
