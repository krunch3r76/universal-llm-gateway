"""Pipeline model availability checks."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

from universal_logging import get_logger

if TYPE_CHECKING:
    from collections.abc import Callable
    from .core.schemas import PipelineSpec

logger = get_logger(__name__)


class ModelRefResolver(Protocol):
    """
    Protocol for model reference resolver return type.

    Objects returned by resolve_model_ref must have a .model attribute
    containing the model ID string.
    """

    model: str


def get_pipeline_required_models(
    pipeline: PipelineSpec,
    *,
    resolve_model_ref: Callable[[str], ModelRefResolver],
) -> set[str]:
    """
    Extract model IDs required by pipeline steps.

    Inputs:
        pipeline: Pipeline specification with steps
        resolve_model_ref: Function mapping model_ref string →
            ModelRefResolver with .model attribute

    Outputs:
        Set of model IDs (str) required by all pipeline steps

    Behavior:
        - Checks steps with direct model_ref
        - Checks map steps with map_inputs.model_ref (extracts from map_over source)
        - Logs warning for unknown model_ref (validation handles separately)
        - Returns empty set if no steps require models

    Returns S = {model_id | ∃ step ∈ pipeline.steps: step.model_ref → model_id}
             ∪ {model_id | ∃ step with map_inputs.model_ref: map_over → model_ids}

    Args:
        pipeline: Pipeline specification
        resolve_model_ref: Function to resolve model_ref to config with .model attr

    Returns:
        Set of model IDs required by pipeline
    """
    required: set[str] = set()
    for step in pipeline.steps:
        # 1. Direct model_ref on step
        if step.model_ref:
            # optionsNs.<key> — resolve via pipeline options before registry lookup
            if step.model_ref.startswith("optionsNs."):
                option_key = step.model_ref[len("optionsNs.") :]
                resolved_ref = pipeline.options.get(option_key)
                if resolved_ref and isinstance(resolved_ref, str):
                    _add_model_from_ref(
                        required, resolved_ref, resolve_model_ref, pipeline.id, step.id
                    )
                else:
                    logger.warning(
                        "Pipeline %s step %s: optionsNs.%s not found in options",
                        pipeline.id,
                        step.id,
                        option_key,
                    )
            else:
                _add_model_from_ref(
                    required, step.model_ref, resolve_model_ref, pipeline.id, step.id
                )

        # 2. Map steps with model_ref in map_inputs
        if step.is_map_step:
            map_config = step.get_map_config()
            if not map_config or not map_config.map_inputs:
                continue

            # Check if map_inputs contains model_ref binding
            if "model_ref" not in map_config.map_inputs:
                continue

            # Extract models from map_over source
            # Pattern: map_over: {model: optionsNs.answer_models}
            #          map_inputs: {model_ref: mapNs.iteration.value}
            # Extract model refs from options.answer_models
            map_over_binding = next(iter(map_config.map_over.values()))
            if map_over_binding.namespace != "optionsNs":
                continue

            # Get the option value (e.g., answer_models dict/list)
            option_path = map_over_binding.field_path
            models_data = pipeline.options.get(option_path)
            if not models_data:
                logger.warning(
                    "Pipeline %s step %s: map_over references unknown option %s",
                    pipeline.id,
                    step.id,
                    option_path,
                )
                continue

            # Extract model refs from dict values or list items
            if isinstance(models_data, dict):
                model_refs = models_data.values()
            elif isinstance(models_data, list):
                model_refs = models_data
            else:
                logger.warning(
                    "Pipeline %s step %s: map_over option %s has unexpected type %s",
                    pipeline.id,
                    step.id,
                    option_path,
                    type(models_data).__name__,
                )
                continue

            # Resolve each model ref
            for model_ref in model_refs:
                if isinstance(model_ref, str):
                    _add_model_from_ref(
                        required, model_ref, resolve_model_ref, pipeline.id, step.id
                    )

    return required


def _add_model_from_ref(
    required: set[str],
    model_ref: str,
    resolve_model_ref: Callable[[str], ModelRefResolver],
    pipeline_id: str,
    step_id: str,
) -> None:
    """Add resolved model ID to required set."""
    config = None
    try:
        config = resolve_model_ref(model_ref)
        required.add(config.model)
    except KeyError:
        logger.warning(
            "Pipeline %s step %s references unknown model_ref %s",
            pipeline_id,
            step_id,
            model_ref,
        )
    except AttributeError as e:
        logger.error(
            "Pipeline %s step %s: resolve_model_ref returned object "
            "without .model attribute: %s",
            pipeline_id,
            step_id,
            e,
        )
        raise TypeError(
            f"resolve_model_ref must return object with .model attribute, "
            f"got {type(config)}"
        ) from e


def are_models_available(
    required_models: set[str],
    *,
    is_available: Callable[[str], bool],
) -> bool:
    """Return True iff every required model ID passes *is_available*.

    *is_available* is injected by Stargate (e.g. ModelId-aware catalog match
    plus registered pipeline virtual IDs). Empty *required_models* → True.
    """
    if not required_models:
        return True
    return all(is_available(mid) for mid in required_models)


def missing_models(
    required_models: set[str],
    *,
    is_available: Callable[[str], bool],
) -> set[str]:
    """Subset of *required_models* for which *is_available* is False."""
    return {mid for mid in required_models if not is_available(mid)}
