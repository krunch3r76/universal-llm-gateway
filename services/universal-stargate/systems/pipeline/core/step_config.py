"""
Step configuration schema.

Contains the StepConfig model and parsing/normalization helpers.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .step_types import InputBinding, MapConfig, OutputBinding

if TYPE_CHECKING:
    from .execution.retry import RetryPolicy


class StepConfig(BaseModel):
    """
    Step configuration for pipeline execution.

    Invariant: ∀ binding ∈ handler_inputs.values(), binding resolved before execute()
    """

    model_config: ConfigDict = ConfigDict(populate_by_name=True, extra="allow")

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

    # Common optional fields
    model_ref: str | None = None
    model_requirements: dict[str, Any] | None = None
    prompt_ref: str | None = None
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
        if v == "map":
            raise ValueError(
                "type='map' is not allowed. MAP is an execution mode, not a "
                "handler type. Use an explicit handler type (e.g., 'type: generate') "
                "plus 'map_over'/'map_inputs' (flat) or 'map_config' (nested)."
            )
        return v

    @model_validator(mode="before")
    @classmethod
    def normalize_map_config(cls, values: dict[str, Any]) -> dict[str, Any]:
        """Normalize flat map_over/map_inputs fields into map_config."""
        # Pydantic's model_validator(mode='before') typically ensures `values` is a dict
        # if the model is being parsed from a dict. If it can be other types, the type hint
        # should be `Any`. Assuming it's always a dict here.
        # if not isinstance(values, dict):
        #     return values
        if values.get("map_config") is not None:
            return values

        if "map_over" in values:
            map_config: dict[str, Any] = {"map_over": values.pop("map_over")}
            if "map_inputs" in values:
                map_config["map_inputs"] = values.pop("map_inputs")
            if "min_success_threshold" in values:
                map_config["min_success_threshold"] = values.pop(
                    "min_success_threshold"
                )
            if "fail_fast" in values:
                map_config["fail_fast"] = values.pop("fail_fast")
            if "model_pool" in values:
                map_config["model_pool"] = values.pop("model_pool")
            if "selection" in values:
                map_config["selection"] = values.pop("selection")
            if "exclude_self" in values:
                map_config["exclude_self"] = values.pop("exclude_self")
            if (
                "model_requirements" in values
                and "model_requirements" not in map_config
            ):
                map_config["model_requirements"] = values.get("model_requirements")
            values["map_config"] = map_config
        return values

    @field_validator("handler_inputs", mode="before")
    @classmethod
    def parse_handler_inputs(cls, v: dict[str, Any]) -> dict[str, InputBinding]:
        """Convert string/dict bindings to InputBinding objects."""
        # Pydantic's field_validator(mode='before') typically ensures `v` is a dict
        # if the field is being parsed from a dict. If it can be other types, the type hint
        # should be `Any`. Assuming it's always a dict here.
        # if not isinstance(v, dict):
        #     return v
        result = {}
        for key, value in v.items():
            if isinstance(value, str):
                result[key] = InputBinding.parse(value)
            elif isinstance(value, InputBinding):
                result[key] = value
            elif isinstance(value, dict):
                result[key] = InputBinding(
                    namespace=value["namespace"],
                    step_name=value.get("step_name"),
                    field_path=value["field_path"],
                )
            else:
                result[key] = value
        return result

    @field_validator("handler_outputs", mode="before")
    @classmethod
    def parse_handler_outputs(cls, v: dict[str, Any]) -> dict[str, OutputBinding]:
        """Convert string/dict specs to OutputBinding objects."""
        # Pydantic's field_validator(mode='before') typically ensures `v` is a dict
        # if the field is being parsed from a dict. If it can be other types, the type hint
        # should be `Any`. Assuming it's always a dict here.
        # if not isinstance(v, dict):
        #     return v
        result = {}
        for key, value in v.items():
            if isinstance(value, str):
                input_binding = InputBinding.parse(value)
                result[key] = OutputBinding(binding=input_binding)
            elif isinstance(value, dict):
                binding_val = value.get("binding")
                optional = value.get("optional", False)
                if binding_val is None:
                    raise ValueError(
                        f"handler_outputs[{key!r}]: dict format requires 'binding' key"
                    )
                if isinstance(binding_val, str):
                    input_binding = InputBinding.parse(binding_val)
                elif isinstance(binding_val, dict):
                    input_binding = InputBinding(
                        namespace=binding_val["namespace"],
                        step_name=binding_val.get("step_name"),
                        field_path=binding_val["field_path"],
                    )
                elif isinstance(binding_val, InputBinding):
                    input_binding = binding_val
                else:
                    raise ValueError(
                        f"handler_outputs[{key!r}]: binding must be str, dict, or "
                        f"InputBinding, got {type(binding_val).__name__}"
                    )
                result[key] = OutputBinding(binding=input_binding, optional=optional)
            elif isinstance(value, OutputBinding):
                result[key] = value
            else:
                result[key] = value
        return result

    @model_validator(mode="after")
    def validate_provenance_config(self) -> Self:
        """Preserve compatibility: handlers validate provenance requirements."""
        return self

    @property
    def id(self) -> str:
        """Alias for name - backward compatibility."""
        return self.name

    @property
    def computed_depends_on(self) -> list[str]:
        """Derive dependencies from bindings, map config, extras, and condition."""
        deps: set[str] = set()

        for binding in self.handler_inputs.values():
            if binding.namespace == "step" and binding.step_name:
                deps.add(binding.step_name)

        if self.map_config:
            for binding_str in self.map_config.get("map_over", {}).values():
                if isinstance(binding_str, str):
                    binding = InputBinding.parse(binding_str)
                    if binding.namespace == "step" and binding.step_name:
                        deps.add(binding.step_name)

        if self.model_extra:
            for key, val in self.model_extra.items():
                if key.endswith("_step") and isinstance(val, str):
                    deps.add(val)
                elif key == "config" and isinstance(val, dict):
                    for config_key, config_val in val.items():
                        if config_key.endswith("_step") and isinstance(config_val, str):
                            deps.add(config_val)

        if self.condition:
            from .conditions import extract_condition_deps

            deps |= extract_condition_deps(self.condition)

        from .handlers.registry import HandlerRegistry

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
    ) -> str | None:
        """Get the model_id this step will invoke.

        Resolution order:
        1. model_ref_overrides (explicit user/caller choice)
        2. model_ref "auto" + model_requirements → /v1/models/select (first candidate)
        3. model_ref → models.yaml registry lookup
        4. None (no model_ref set and no model_requirements)
        """
        if model_ref_overrides and self.model_ref:
            override = model_ref_overrides.get(self.name) or model_ref_overrides.get(
                self.model_ref
            )
            if isinstance(override, str) and override.strip():
                return override.strip()

        if self.model_ref == "auto" or (not self.model_ref and self.model_requirements):
            from .execution.requirements_resolver import resolve_model_requirements

            candidates = resolve_model_requirements(self.model_requirements or {})
            return candidates[0] if candidates else None

        if not self.model_ref:
            return None
        self.validate_model_ref()

        try:
            model_config = registry.get_model_config(
                self.model_ref, domain=domain, search_path=search_path
            )
            return model_config.model if model_config else None
        except Exception:
            return None

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
        # Refactor to avoid duplication with get_target_model_id
        # Common logic for model_ref_overrides and direct model_ref handling
        # could be in a helper, with async-specific parts handled here.
        if model_ref_overrides and self.model_ref:
            override = model_ref_overrides.get(self.name) or model_ref_overrides.get(
                self.model_ref
            )
            if isinstance(override, str) and override.strip():
                return override.strip()

        if self.model_ref == "auto" or (not self.model_ref and self.model_requirements):
            from .execution.requirements_resolver import (
                async_resolve_model_requirements,
            )
            from .execution.resolved_candidates import get_ranked_candidates

            requirements = dict(self.model_requirements or {})
            if context is None:
                candidates = await async_resolve_model_requirements(requirements)
            else:
                candidates = await get_ranked_candidates(
                    context=context,
                    step_name=self.name,
                    requirements=requirements,
                )
            return candidates[0] if candidates else None

        if not self.model_ref:
            return None
        self.validate_model_ref()

        try:
            model_config = registry.get_model_config(
                self.model_ref, domain=domain, search_path=search_path
            )
            return model_config.model if model_config else None
        except Exception:
            return None

    def get_retry_policy(self) -> RetryPolicy | None:
        """Parse retry_policy dict to RetryPolicy object."""
        if not self.retry_policy:
            return None
        from .execution.retry import RetryPolicy

        return RetryPolicy(**self.retry_policy)

    def get_map_config(self) -> MapConfig | None:
        """Parse map_config dict to MapConfig object."""
        if not self.map_config:
            return None

        raw = self.map_config

        map_over: dict[str, InputBinding] = {}
        for field, binding in raw.get("map_over", {}).items():
            if isinstance(binding, str):
                map_over[field] = InputBinding.parse(binding)
            elif isinstance(binding, InputBinding):
                map_over[field] = binding
            else:
                raise TypeError(
                    f"map_over[{field!r}]: expected str or InputBinding, "
                    f"got {type(binding).__name__}"
                )

        map_inputs: dict[str, InputBinding] = {}
        for field, binding in raw.get("map_inputs", {}).items():
            if isinstance(binding, str):
                map_inputs[field] = InputBinding.parse(binding)
            elif isinstance(binding, InputBinding):
                map_inputs[field] = binding
            else:
                raise TypeError(
                    f"map_inputs[{field!r}]: expected str or InputBinding, "
                    f"got {type(binding).__name__}"
                )

        model_pool: InputBinding | None = None
        if "model_pool" in raw:
            pool_val = raw["model_pool"]
            if isinstance(pool_val, str):
                model_pool = InputBinding.parse(pool_val)
            elif isinstance(pool_val, InputBinding):
                model_pool = pool_val
            elif pool_val is not None:
                raise TypeError(
                    f"model_pool: expected str or InputBinding, "
                    f"got {type(pool_val).__name__}"
                )

        timeout_seconds = raw.get("timeout_seconds")
        if timeout_seconds is None:
            timeout_seconds = self.timeout_seconds

        return MapConfig(
            map_over=map_over,
            map_inputs=map_inputs,
            timeout_seconds=timeout_seconds,
            min_success_threshold=raw.get("min_success_threshold"),
            fail_fast=raw.get("fail_fast", False),
            model_pool=model_pool,
            model_requirements=raw.get("model_requirements"),
            exclude_self=raw.get("exclude_self", False),
            selection=raw.get("selection", "rotate"),
        )
