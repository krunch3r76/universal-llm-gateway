"""Pipeline model availability checks."""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Protocol

from universal_logging import get_logger

if TYPE_CHECKING:
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
    gateway_catalogs: list[set[str]],
) -> bool:
    """
    Check if all required models are available across gateways.

    Let G = ⋃ gateway_catalogs. Returns True iff required_models ⊆ G.

    Inputs:
        required_models: Set of model IDs needed (empty = always available)
        gateway_catalogs: List of model sets from each connected gateway

    Outputs:
        True if required_models ⊆ ⋃gateway_catalogs, False otherwise

    Edge cases:
        - Empty required_models → True (no requirements)
        - Empty gateway_catalogs → False (no gateways = no models available)
        - Partial match → False (all must be available)

    Args:
        required_models: Set of model IDs needed
        gateway_catalogs: List of model sets from each gateway

    Returns:
        True if all required models available, False otherwise
    """
    if not required_models:
        return True  # No requirements = always available

    # Handle empty gateway_catalogs (no gateways connected)
    if not gateway_catalogs:
        logger.debug("are_models_available: No gateways connected - returning False")
        return False  # No gateways = no models available

    available: set[str] = set().union(*gateway_catalogs)

    # DETAILED DEBUG: Log exact required models for debugging
    logger.debug(
        f"are_models_available: DETAILED required_models={sorted(required_models)}"
    )

    # Check each required model individually for detailed diagnostics
    missing: set[str] = set()
    for req_model in required_models:
        if req_model not in available:
            missing.add(req_model)
            # Find similar models for debugging
            similar = [m for m in available if req_model.rsplit("-", 1)[0] in m][:3]
            logger.debug(
                f"are_models_available: Model '{req_model}' NOT FOUND. "
                f"Similar in catalog: {similar}"
            )
        else:
            logger.debug(f"are_models_available: Model '{req_model}' FOUND in catalog")

    result = len(missing) == 0

    if not result:
        logger.debug(
            f"are_models_available: FALSE - required={len(required_models)}, "
            f"available={len(available)}, missing={len(missing)}: {sorted(missing)}"
        )
        # Debug: show some available models for comparison
        sample_available = sorted(available)[:10]
        logger.debug(
            f"are_models_available: Available models sample (first 10): "
            f"{sample_available}"
        )
    else:
        logger.debug(
            f"are_models_available: TRUE - all {len(required_models)} models available"
        )

    return result
