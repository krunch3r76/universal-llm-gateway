"""
Pipeline validation logic for the registry subsystem.

Validates pipeline configurations: handler existence, model/prompt references,
DAG cycles, output step validity, and cross-version prompt isolation.
Part of the pipeline registry package.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .core import PipelineRegistry

from universal_logging import get_logger

from ..core.dag import DAGBuilder
from ..core.handlers import HandlerRegistry
from ..core.schemas import PipelineSpec

logger = get_logger(__name__)


class PipelineConfigError(Exception):
    """Raised when pipeline configuration validation fails.

    Indicates an issue with the structure, references, or logic within a
    pipeline's YAML configuration, preventing it from being loaded or
    executed correctly. Typically caught during the registry's
    initialization or reload process.
    """

    pass


def _collect_prompt_refs(
    data: dict[str, str | dict[str, Any]],
    prefix: str = "",
) -> list[tuple[str, str]]:
    """Recursively collect (path, value) for prompt_ref* keys."""
    refs: list[tuple[str, str]] = []
    for key, val in data.items():
        path = f"{prefix}.{key}" if prefix else key
        if key.startswith("prompt_ref") and isinstance(val, str):
            refs.append((path, val))
        elif isinstance(val, dict):
            refs.extend(_collect_prompt_refs(val, path))
    return refs


class PipelineValidator:
    """
    Validates loaded pipeline configurations.

    Checks handler existence, depends_on/inputs references, DAG cycles,
    model_ref/prompt_ref resolution, and cross-version prompt isolation.
    """

    def __init__(self, registry_instance: PipelineRegistry) -> None:
        self._registry = registry_instance

    def _validate_all_pipelines(self) -> None:
        """
        Validate all loaded pipelines.

        Checks:
        - Handler exists for (pipeline.type, step.type)
        - depends_on references valid step IDs
        - No cycles in dependencies
        - model_ref exists in models
        - prompt_ref exists in prompts
        """
        for pipeline_id, pipeline in self._registry.pipelines.items():
            self._registry._validation_errors.extend(
                [f"[{pipeline_id}] {e}" for e in self._validate_pipeline(pipeline)]
            )

    def _remove_invalid_pipelines(self) -> set[str]:
        """
        Remove pipelines with validation errors.

        Extracts pipeline IDs from validation errors and removes them from registry.
        Allows valid pipelines to remain usable despite some invalid configurations.

        Returns:
            Set of removed pipeline IDs for logging/events
        """
        invalid_pipeline_ids = {
            err[1 : err.index("]")]
            for err in self._registry._validation_errors
            if err.startswith("[") and "]" in err
        }

        removed_ids = set()
        for pipeline_id in invalid_pipeline_ids:
            if pipeline_id in self._registry.pipelines:
                del self._registry.pipelines[pipeline_id]
                removed_ids.add(pipeline_id)
                logger.debug(f"Removed invalid pipeline from registry: {pipeline_id}")

        return removed_ids

    def _validate_pipeline(self, pipeline: PipelineSpec) -> list[str]:
        """Validate a single pipeline configuration."""
        errors = []
        step_ids = {step.id for step in pipeline.steps}

        for step in pipeline.steps:
            if step.type == "sub_pipeline":
                continue

            handler_class = HandlerRegistry.get_class(
                pipeline.type, step.type, variant=pipeline.source_variant
            )
            if handler_class is None:
                errors.append(
                    f"Step '{step.id}': No handler for type '{step.type}' "
                    f"in domain '{pipeline.type}' variant '{pipeline.source_variant}'"
                )
                continue

            handler = handler_class()
            if hasattr(handler, "validate"):
                step_errors = handler.validate(step)
                errors.extend(step_errors)

            if step.model_ref:
                model_ref_to_check = step.model_ref
                if model_ref_to_check.startswith("optionsNs."):
                    option_key = model_ref_to_check[len("optionsNs.") :]
                    resolved = pipeline.options.get(option_key)
                    if resolved and isinstance(resolved, str):
                        model_ref_to_check = resolved
                    else:
                        errors.append(
                            f"Step '{step.id}': Unknown model_ref '{step.model_ref}' "
                            f"(option '{option_key}' not found in pipeline options)"
                        )
                        continue
                try:
                    self._registry.get_model_config(
                        model_ref_to_check,
                        domain=pipeline.domain,
                        search_path=pipeline.source_search_path,
                    )
                except KeyError:
                    errors.append(
                        f"Step '{step.id}': Unknown model_ref '{step.model_ref}'"
                    )

            if step.prompt_ref:
                if not ("{{" in step.prompt_ref and "}}" in step.prompt_ref):
                    try:
                        self._registry.get_prompt(step.prompt_ref)
                    except KeyError:
                        errors.append(
                            f"Step '{step.id}': Unknown prompt_ref '{step.prompt_ref}'"
                        )
                    except ValueError as e:
                        errors.append(f"Step '{step.id}': {e}")

            for dep_id in step.depends_on:
                if dep_id not in step_ids:
                    errors.append(
                        f"Step '{step.id}': depends_on unknown step '{dep_id}'"
                    )

            if step.inputs:
                for input_id in step.inputs:
                    if input_id not in step_ids:
                        errors.append(
                            f"Step '{step.id}': input '{input_id}' is not a valid step"
                        )

        try:
            DAGBuilder(pipeline.steps, validate_only=True).build()
        except ValueError as e:
            errors.append(f"DAG error: {e}")

        output_step_name = (
            pipeline.output.split(".")[0] if "." in pipeline.output else pipeline.output
        )
        if output_step_name not in step_ids:
            errors.append(
                f"Output step '{pipeline.output}' not found in steps "
                f"(expected step name: '{output_step_name}')"
            )

        if pipeline.source_variant:
            expected_prefix = f"{pipeline.type}.{pipeline.source_variant}."
            for step in pipeline.steps:
                if step.type == "sub_pipeline":
                    continue
                all_refs: list[tuple[str, str]] = []
                if step.prompt_ref and "{{" not in step.prompt_ref:
                    all_refs.append(("prompt_ref", step.prompt_ref))
                if step.model_extra:
                    all_refs.extend(_collect_prompt_refs(step.model_extra))
                for field, ref in all_refs:
                    if "{{" in ref:
                        continue
                    if not ref.startswith(expected_prefix):
                        errors.append(
                            f"Step '{step.id}': {field} '{ref}' references "
                            f"outside version namespace '{expected_prefix[:-1]}'"
                        )

        return errors
