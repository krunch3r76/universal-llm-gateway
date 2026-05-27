"""StepConfig schema — pipeline step definition consumed by the DAG executor.

This module declares the :class:`StepConfig` Pydantic model that backs every
entry in a pipeline YAML's ``steps:`` block. Field declarations live here so
the schema (and Pydantic-derived validators) registers at class definition
time; parsing/normalization bodies, model resolution, and map-config building
delegate to focused sibling modules to keep this file scoped to the schema
surface.

Public re-exports flow through the package ``__init__``:
``from .step_config import StepConfig`` and
``from .step_config import ResolvedTargetModel`` continue to work for every
consumer under ``core/`` without import-path changes.
"""
# ruff: noqa: E501

from __future__ import annotations

from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from ..execution.retry import RetryPolicy
from ..step_types import (
    InputBinding,
    MapConfig,
    OutputBinding,
    OutputDeclaration,
    ReadsFrom,
)
from . import parsing_validators
from .map_config_builder import build_map_config
from .model_resolution import (
    resolve_target_model_async,
    resolve_target_model_sync,
)
from .resolved_target_model import ResolvedTargetModel


class StepConfig(BaseModel):
    """
    Step configuration for pipeline execution.

    Invariant: ∀ binding ∈ handler_inputs.values(), binding resolved before execute()
    """

    model_config: ConfigDict = ConfigDict(populate_by_name=True, extra="allow")
    """Pydantic model configuration.

    - `populate_by_name`: Allows fields to be populated by their original name (e.g., 'id')
      even if an alias (e.g., 'name') is defined.
    - `extra='allow'`: Permits additional fields not explicitly defined in the schema,
      which are stored in `model_extra`.
    """

    # Required
    name: str = Field(alias="id")
    type: str

    # Handler specification
    handler: str | None = None
    handler_inputs: dict[str, InputBinding] = Field(default_factory=dict)
    avoid_models_from: str | None = Field(
        default=None,
        description=(
            "Binding path to a prior step's model_id. "
            "Resolved at execution time and passed as avoid_models "
            "to /v1/models/select, ensuring model diversity in chained steps. "
            "Example: 'analyze.model_id'"
        ),
    )
    handler_outputs: dict[str, OutputBinding] = Field(default_factory=dict)

    # Step contracts (schema_version 6+, optional)
    output_declarations: dict[str, OutputDeclaration] = Field(default_factory=dict)
    reads_from: list[ReadsFrom] = Field(default_factory=list)

    # Common optional fields
    model_ref: str | None = None
    model_requirements: dict[str, Any] | None = None
    prompt_ref: str | None = None
    system_prompt: str | None = None
    condition: str | None = None
    skip_token_counting: bool | None = None
    disable_profile: bool | None = None
    profile: str | None = None
    inputs: list[str] | None = None
    from_: str | None = Field(None, alias="from")
    source_text_id: str | None = None
    generation_parameters: dict[str, Any] = Field(default_factory=dict)

    # Provenance
    provenance_mode: Literal["create", "preserve", "aggregate"] | None = None
    originator_mapping: dict[str, str] | None = None

    # Retry & Timeout
    retry_policy: dict[str, Any] | None = None
    timeout_seconds: float | None = None
    handler_timeout_seconds: float | None = None

    # Checkpoint
    checkpoint: bool | Literal["milestone"] | None = None

    # Map/Reduce
    map_config: dict[str, Any] | None = None
    resolved_map_inputs: dict[str, Any] = Field(default_factory=dict)

    # Legacy
    depends_on: list[str] = Field(default_factory=list)

    @field_validator("type")
    @classmethod
    def reject_map_type(cls, v: str) -> str:
        """Reject type='map' — map is an execution mode, not a handler type."""
        return parsing_validators.reject_map_type(v)

    @model_validator(mode="before")
    @classmethod
    def normalize_map_config(cls, values: dict[str, Any]) -> dict[str, Any]:
        """Normalize flat map_over/map_inputs fields into map_config."""
        return parsing_validators.normalize_map_config(values)

    @field_validator("handler_inputs", mode="before")
    @classmethod
    def parse_handler_inputs(cls, v: dict[str, Any]) -> dict[str, InputBinding]:
        """Convert string/dict bindings to InputBinding objects."""
        return parsing_validators.parse_handler_inputs(v)

    @field_validator("handler_outputs", mode="before")
    @classmethod
    def parse_handler_outputs(cls, v: dict[str, Any]) -> dict[str, OutputBinding]:
        """Convert string/dict specs to OutputBinding objects."""
        return parsing_validators.parse_handler_outputs(v)

    @field_validator("output_declarations", mode="before")
    @classmethod
    def parse_output_declarations(
        cls,
        v: dict[str, Any],
    ) -> dict[str, OutputDeclaration]:
        """Convert dict specs to OutputDeclaration objects."""
        return parsing_validators.parse_output_declarations(v)

    @field_validator("reads_from", mode="before")
    @classmethod
    def parse_reads_from(cls, v: list[Any]) -> list[ReadsFrom]:
        """Convert dict specs to ReadsFrom objects."""
        return parsing_validators.parse_reads_from(v)

    @model_validator(mode="after")
    def validate_provenance_config(self) -> Self:
        """Preserve compatibility: handlers validate provenance requirements."""
        if "messages" in self.handler_inputs:
            if self.get_domain_field("pass_messages"):
                raise ValueError(
                    f"Step '{self.name}': handler_inputs.messages and pass_messages "
                    f"are mutually exclusive. Choose only one."
                )
        return self

    @property
    def id(self) -> str:
        """Alias for name - backward compatibility."""
        return self.name

    @property
    def computed_depends_on(self) -> list[str]:
        """Derive deps from bindings, map config, reads_from, extras, condition."""
        deps: set[str] = set()

        for binding in self.handler_inputs.values():
            if binding.namespace == "step" and binding.step_name:
                deps.add(binding.step_name)

        for rf in self.reads_from:
            deps.add(rf.step)

        if self.map_config:
            for binding_str in self.map_config.get("map_over", {}).values():
                if isinstance(binding_str, str):
                    binding = InputBinding.parse(binding_str)
                    if binding.namespace == "step" and binding.step_name:
                        deps.add(binding.step_name)

        if isinstance(self.model_extra, dict):
            for key, val in self.model_extra.items():
                if key.endswith("_step") and isinstance(val, str):
                    deps.add(val)
                elif key == "config" and isinstance(val, dict):
                    for config_key, config_val in val.items():
                        if config_key.endswith("_step") and isinstance(config_val, str):
                            deps.add(config_val)

        if self.condition:
            from ..conditions import extract_condition_deps

            deps |= extract_condition_deps(self.condition)

        from ..handlers.registry import HandlerRegistry

        for field_name in HandlerRegistry.get_handler_dependency_fields(self.type):
            field_val = self.get_domain_field(field_name)
            if isinstance(field_val, list):
                for item in field_val:
                    if isinstance(item, str):
                        deps.add(item)

        return list(deps)

    @property
    def is_map_step(self) -> bool:
        """Map detection is based on presence of map_config."""
        return self.map_config is not None

    def get_domain_field(self, field: str, default: Any = None) -> Any:
        """Get domain-specific field from extras."""
        if self.model_extra is None:
            return default
        return self.model_extra.get(field, default)

    def validate_model_ref(self) -> None:
        """Validate that model_ref is static."""
        if self.model_ref and "${" in self.model_ref:
            raise ValueError(
                f"Step '{self.name}': Dynamic model_ref not supported. "
                f"Got: {self.model_ref}"
            )

    def get_target_model_id(
        self,
        registry: Any,
        *,
        domain: str | None = None,
        search_path: str | None = None,
        model_ref_overrides: dict[str, str] | None = None,
        context: Any | None = None,
    ) -> str | None:
        """Get the model_id this step will invoke.

        Resolution order:
        1. pipeline runtime override when handler semantics honor it
        2. model_ref_overrides (explicit user/caller choice)
        3. model_ref "auto" + model_requirements → /v1/models/select (first candidate)
        4. model_ref → models.yaml registry lookup
        5. None (no model_ref set and no model_requirements)
        """
        resolved = self.get_target_model_resolution(
            registry,
            domain=domain,
            search_path=search_path,
            model_ref_overrides=model_ref_overrides,
            context=context,
        )
        return resolved.model_id if resolved else None

    def get_target_model_resolution(
        self,
        registry: Any,
        *,
        domain: str | None = None,
        search_path: str | None = None,
        model_ref_overrides: dict[str, str] | None = None,
        context: Any | None = None,
    ) -> ResolvedTargetModel | None:
        """Return structured target-model resolution metadata for this step."""
        return resolve_target_model_sync(
            self,
            registry,
            domain=domain,
            search_path=search_path,
            model_ref_overrides=model_ref_overrides,
            context=context,
        )

    async def get_target_model_id_async(
        self,
        registry: Any,
        *,
        domain: str | None = None,
        search_path: str | None = None,
        model_ref_overrides: dict[str, str] | None = None,
        context: Any | None = None,
    ) -> str | None:
        """Async target-model resolution for live execution paths.

        ∀ event-loop callers: use this variant so `model_requirements` selection
        yields while `/v1/models/select` is served by the same Stargate process.
        """
        resolved = await self.get_target_model_resolution_async(
            registry,
            domain=domain,
            search_path=search_path,
            model_ref_overrides=model_ref_overrides,
            context=context,
        )
        return resolved.model_id if resolved else None

    async def get_target_model_resolution_async(
        self,
        registry: Any,
        *,
        domain: str | None = None,
        search_path: str | None = None,
        model_ref_overrides: dict[str, str] | None = None,
        context: Any | None = None,
    ) -> ResolvedTargetModel | None:
        """Async structured target-model resolution for live execution paths."""
        return await resolve_target_model_async(
            self,
            registry,
            domain=domain,
            search_path=search_path,
            model_ref_overrides=model_ref_overrides,
            context=context,
        )

    def get_retry_policy(self) -> RetryPolicy | None:
        """Parse retry_policy dict to RetryPolicy object."""
        if not self.retry_policy:
            return None
        return RetryPolicy(**self.retry_policy)

    def get_map_config(self) -> MapConfig | None:
        """Parse map_config dict to MapConfig object."""
        return build_map_config(self)
