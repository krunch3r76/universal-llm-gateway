"""
Pipeline access logic for the registry subsystem.

Provides methods for accessing loaded pipeline configurations,
model configurations, and prompts. Includes error handling for not-found scenarios.
Part of the pipeline registry package.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Never

from universal_logging import get_logger

from ..core.schemas import PipelineSpec, PromptConfig
from ..schemas import ModelRef

if TYPE_CHECKING:
    from .core import PipelineRegistry

logger = get_logger(__name__)


class PipelineAccessor:
    """
    Provides read access to loaded pipeline configurations.

    Handles get_pipeline, get_model_config, get_prompt, unavailable_pipelines,
    and is_pipeline with proper error handling and guidance.
    """

    def __init__(self, registry_instance: PipelineRegistry) -> None:
        self._registry = registry_instance

    def get_pipeline(self, pipeline_id: str) -> PipelineSpec:
        """Get pipeline by ID."""
        if pipeline_id not in self._registry.pipelines:
            raise KeyError(f"Pipeline '{pipeline_id}' not found")
        return self._registry.pipelines[pipeline_id]

    def get_model_config(
        self,
        model_ref: str,
        *,
        domain: str | None = None,
        search_path: str | None = None,
    ) -> ModelRef:
        """
        Get model configuration with search-path-scoped resolution.

        Invariant: resolution restricted to models from `search_path` only.
        Each search path is an isolated namespace — no cross-path fallback.

        Resolution order (within the given search path):
        1. Domain-qualified: "{domain}.{ref}" in domain models
        2. Root namespace: "{ref}" in root models
        3. Explicit qualified: "{ref}" in domain models (cross-domain)
        4. KeyError with guidance

        Args:
            model_ref: Reference like "qwen" or "translation.qwen"
            domain: Pipeline domain for auto-resolution (e.g., "consensus")
            search_path: Search path name to scope resolution (e.g., "pipelines.local")

        Returns:
            ModelRef configuration

        Raises:
            KeyError: If model ref not found, with available options
        """
        domain_bucket = self._registry._domain_models.get(search_path or "", {})
        root_bucket = self._registry._root_models.get(search_path or "", {})

        if domain:
            qualified = f"{domain}.{model_ref}"
            if qualified in domain_bucket:
                return domain_bucket[qualified]

        if model_ref in root_bucket:
            return root_bucket[model_ref]

        if model_ref in domain_bucket:
            return domain_bucket[model_ref]

        self._raise_model_not_found(model_ref, domain, search_path)

    def _raise_model_not_found(
        self, model_ref: str, domain: str | None, search_path: str | None
    ) -> Never:
        """
        Construct a detailed error message and raise KeyError when a model
        reference cannot be resolved within the specified scope.

        The message includes context about the missing model, hints on where
        to define it, and lists available model references for debugging.

        Args:
            model_ref: The unresolvable model reference string.
            domain: The domain context in which resolution was attempted.
            search_path: The search path context in which resolution was attempted.

        Raises:
            KeyError: Always, with a detailed diagnostic message.
        """
        sp = search_path or ""
        domain_bucket = self._registry._domain_models.get(sp, {})
        root_bucket = self._registry._root_models.get(sp, {})

        root_refs = sorted(root_bucket.keys())
        domain_refs = (
            sorted(
                k.split(".", 1)[1] for k in domain_bucket if k.startswith(f"{domain}.")
            )
            if domain
            else []
        )
        all_domains = sorted(set(k.split(".")[0] for k in domain_bucket))

        msg_parts = [f"Model ref '{model_ref}' not found"]
        if domain:
            msg_parts.append(f" in domain '{domain}'")
        if search_path:
            msg_parts.append(f" (search path: '{search_path}')")
        msg_parts.append(".\n")

        if domain and search_path:
            msg_parts.append(
                f"  Define in: {search_path}/{domain}/models.yaml "
                f"or {search_path}/models.yaml\n"
            )
        elif domain:
            msg_parts.append(
                f"  Define in: <search_path>/{domain}/models.yaml "
                f"(empty source_search_path; check pipelines.search_paths).\n"
            )
        else:
            hint = (
                f"  Define in: {search_path}/models.yaml (root)\n"
                if search_path
                else "  Define in: <search_path>/models.yaml (root).\n"
            )
            msg_parts.append(hint)

        if domain_refs:
            msg_parts.append(f"  Available in '{domain}': {domain_refs}\n")
        if root_refs:
            msg_parts.append(f"  Available in root: {root_refs[:10]}")
            if len(root_refs) > 10:
                msg_parts.append(f" ... (+{len(root_refs) - 10} more)")
            msg_parts.append("\n")
        if all_domains:
            msg_parts.append(f"  Other domains: {all_domains}")

        raise KeyError("".join(msg_parts))

    def get_prompt(self, prompt_ref: str) -> PromptConfig:
        """
        Get structured prompt configuration by reference.

        Supports dotted notation: "domain.prompt_name" or "namespace.subkey"

        Args:
            prompt_ref: Reference like "consensus.statement_generation"

        Returns:
            PromptConfig with all prompt metadata

        Raises:
            KeyError: If prompt not found
            ValueError: If prompt uses deprecated flat string format

        Invariants:
        - ∀ ref ∈ valid_prompts: returns PromptConfig
        - ∀ ref ∉ valid_prompts: raises KeyError
        - ∀ prompt as str: raises ValueError (old format rejected)
        - ∀ prompt without 'template': raises ValueError
        """
        parts = prompt_ref.split(".")
        obj: Any = self._registry.prompts

        for part in parts:
            if isinstance(obj, dict) and part in obj:
                obj = obj[part]
            else:
                raise KeyError(f"Prompt '{prompt_ref}' not found")

        if isinstance(obj, str):
            raise ValueError(
                f"Prompt '{prompt_ref}' uses deprecated flat string format. "
                f"Convert to structured format with 'template' and optional"
                f" 'system_prompt'. generation_parameters and response_format"
                f" must be in step config, not prompts.yaml."
            )

        if not isinstance(obj, dict):
            raise ValueError(
                f"Prompt '{prompt_ref}' must be dict with 'template' field, "
                f"got {type(obj).__name__}"
            )

        if "template" not in obj:
            raise ValueError(
                f"Prompt '{prompt_ref}' missing required 'template' field. "
                f"Structured prompts must have at minimum: template"
            )

        if "generation_parameters" in obj:
            raise ValueError(
                f"Prompt '{prompt_ref}' contains 'generation_parameters'. "
                f"Use step config (pipeline YAML). See README.md#generation_parameters"
            )

        if "json_schema" in obj:
            raise ValueError(
                f"Prompt '{prompt_ref}' contains 'json_schema' field. "
                f"Move to step config: generation_parameters.response_format.schema"
            )

        system_prompt_value = obj.get("system_prompt")

        prompt_name = prompt_ref.split(".")[-1]
        return PromptConfig(
            name=prompt_name,
            description=obj.get("description", ""),
            system_prompt=system_prompt_value,
            template=obj["template"],
        )

    @property
    def unavailable_pipelines(self) -> list[tuple[str, list[str]]]:
        """Pipelines permanently dropped after retry.

        Returns list of (pipeline_id, missing_model_ids) for each pipeline that
        was deferred, retried, and still could not load due to unresolvable deps.
        Callers (e.g. component_factory) may emit structured events for these.
        """
        return list(self._registry._permanently_unavailable)

    def is_pipeline(self, model_id: str) -> bool:
        """Check if model_id refers to a pipeline."""
        return model_id in self._registry.pipelines
