"""
Domain-agnostic pipeline schemas.

Key design decisions:
- type fields are `str`, not `Literal` - open for extension
- options is `dict[str, Any]` - domain-specific
- extra="allow" - domain fields captured automatically
- depends_on is REQUIRED - no implicit dependency inference

Invariants:
- ∀ pipeline: pipeline.type ∈ str (any domain)
- ∀ step: step.type ∈ str (any step type)
- ∀ step: step.depends_on explicitly declared
- Domain-specific fields accessed via model_extra
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from dataclasses import field as dataclass_field
from typing import TYPE_CHECKING, Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

if TYPE_CHECKING:
    from .execution.map_reduce import MapIterationContext
    from .execution.retry import RetryPolicy


class PipelineOptions(BaseModel):
    """
    Generic pipeline options.

    Common fields are explicit; domain-specific fields in extras.

    Note: Pipeline responses are strictly OpenAI-compliant. No custom
    fields (like pipeline.messages) are included. Clients maintain
    conversation history as with standard OpenAI API.
    """

    model_config = ConfigDict(extra="allow")

    # Common options (domain-agnostic)
    include_alternates: bool = False
    include_step_stats: bool = False  # Per-step token breakdown in response
    # include_user_message removed - strict OpenAI compliance
    timeout_seconds: int = 60
    max_tokens: int | None = None
    skip_token_counting: bool = False
    save_execution_summary: bool = False  # Write detailed execution log to disk
    summary_format: str = (
        "markdown"  # Format: "markdown" (default), "yaml", "json", or "all"
    )

    def get(self, key: str, default: Any = None) -> Any:
        """Get option by key (explicit or extra)."""
        if hasattr(self, key):
            return getattr(self, key)
        return self.model_extra.get(key, default)

    def to_context_dict(self) -> dict[str, Any]:
        """
        Convert to dict for prompt context.

        Handles schema definitions (dicts with 'type', 'description', 'default')
        by extracting the 'default' value instead of returning the schema dict.
        """
        result = self.model_dump()
        result.update(self.model_extra)

        # Extract defaults from schema definitions
        # Schema format: {"type": "...", "description": "...", "default": value}
        for key, value in list(result.items()):
            if isinstance(value, dict) and "default" in value and "type" in value:
                # This is a schema definition, extract the default value
                result[key] = value["default"]

        return result


class FragmentRef(BaseModel):
    """Reference to a reusable pipeline fragment."""

    use: str
    with_: dict[str, Any] = Field(default_factory=dict, alias="with")
    as_prefix: str | None = None


class PipelineSpec(BaseModel):
    """
    Generic pipeline specification.

    The `type` field determines which domain handles execution.
    """

    model_config = ConfigDict(extra="allow")

    id: str
    version: str
    type: str  # Open - "translation", "code_review", "multimodal", etc.
    options: PipelineOptions = Field(default_factory=PipelineOptions)
    steps: list[StepConfig]
    output: str

    # Fragment definitions within this pipeline
    fragments: dict[str, list[dict]] | None = None

    # Checkpoint (Phase 3)
    checkpoint: dict[str, Any] | None = None  # Checkpoint configuration

    # Which search path this pipeline was loaded from (e.g. "pipelines.local")
    # Used for search-path-scoped model resolution (isolation semantics)
    source_search_path: str = ""

    # Which variant directory this pipeline was loaded from (e.g. "v6.0")
    # Used for variant-scoped handler dispatch (isolation semantics)
    source_variant: str = ""

    @property
    def domain(self) -> str:
        """Alias for type - clarifies domain routing."""
        return self.type


class SubPipelineSpec(BaseModel):
    """Sub-pipeline specification loaded from a separate YAML file.

    Lighter than PipelineSpec: declares an inputs/outputs interface
    so a parent pipeline step can bind its data flow declaratively.

    Invariant: ∀ step ∈ steps: internal bindings use step names
    local to this sub-pipeline (no parent awareness).
    """

    model_config = ConfigDict(extra="allow")

    id: str
    type: str
    inputs: list[str]
    steps: list[StepConfig]
    output: str


@dataclass
class PromptConfig:
    """
    Structured prompt configuration.

    Replaces flat string prompts with self-contained configuration
    that includes all context needed for model invocation.

    Invariants:
    - ∀ p: PromptConfig, p.template ≠ ∅
    - system_prompt may be None
    - json_schema present ⟹ response expects JSON

    Generation parameters REMOVED - now step-config-only.

    Attributes:
        name: Prompt identifier (last part of prompt_ref)
        description: Human-readable description
        system_prompt: Optional system message for model
        template: User prompt template with {placeholders}
        json_schema: Optional JSON schema for structured output
    """

    name: str
    description: str = ""
    system_prompt: str | None = None
    template: str = ""
    json_schema: dict | None = None

    def __post_init__(self):
        """Validate configuration after initialization."""
        if not self.template or not self.template.strip():
            raise ValueError(
                f"PromptConfig '{self.name}' has empty template. "
                f"Template is required for all prompts."
            )

        if self.json_schema is not None and not isinstance(self.json_schema, dict):
            raise TypeError(
                f"PromptConfig '{self.name}' json_schema must be dict, "
                f"got {type(self.json_schema).__name__}"
            )


# Phase 1: Pipeline Object Flow - Core Schema Components


@dataclass
class StepInputs:
    """
    Base class for typed handler inputs.

    Handlers should subclass this for type-safe input handling.
    Provides fingerprinting for checkpoint cache keys.
    """

    def fingerprint(self) -> str:
        """
        Generate unique fingerprint for these inputs.

        Override in subclass for semantic fingerprinting.
        Default: hash including primitive values for better uniqueness.

        Returns:
            16-character hash (64 bits) for collision resistance
        """
        import hashlib

        # Include actual values for primitives, types for complex objects
        parts = []
        for k, v in sorted(vars(self).items()):
            if isinstance(v, str | int | float | bool | type(None)):
                # Include actual value for primitives
                parts.append(f"{k}={v}")
            else:
                # Type name only for complex objects
                parts.append(f"{k}:{type(v).__name__}")

        # Use 16 chars (64 bits) for better collision resistance
        return hashlib.sha256(":".join(parts).encode()).hexdigest()[:16]


@dataclass
class StepOutput:
    """
    Output from an executed step.

    Supports both dict-style and attribute access for traverse_path compatibility.
    """

    data: dict[str, Any]  # Handler return value
    metadata: dict[str, Any] = dataclass_field(default_factory=dict)

    # Support dict-style access for traverse_path
    def __getitem__(self, key: str) -> Any:
        return self.data[key]

    def __contains__(self, key: str) -> bool:
        return key in self.data

    def get(self, key: str, default: Any = None) -> Any:
        return self.data.get(key, default)

    def to_checkpoint_raw(self) -> str:
        """Extract raw text for checkpointing."""
        if isinstance(self.data, dict):
            if "text" in self.data:
                return str(self.data["text"])
            # Serialize entire dict if no text field
            return json.dumps(self.data)
        return str(self.data)

    def to_checkpoint_json(self) -> dict | None:
        """Extract JSON data for checkpointing."""
        return self.data if isinstance(self.data, dict) else None

    def to_checkpoint_data(self) -> tuple[str, dict | None, dict]:
        """
        Extract checkpoint data from output.

        Returns:
            Tuple of (raw_text, json_data, metadata)
        """
        return (self.to_checkpoint_raw(), self.to_checkpoint_json(), self.metadata)


@dataclass(frozen=True, slots=True)
class InputBinding:
    """
    Binds a handler input field to a data source.

    Invariant:
        namespace ∈ {"sourceNs", "optionsNs", "loopNs", "mapNs"}
        ∨ (namespace == "step" ∧ step_name ≠ None)

    Examples:
        sourceNs.text →
            InputBinding(namespace="sourceNs", step_name=None, field_path="text")
        merge.json.statements →
            InputBinding(namespace="step", step_name="merge",
                        field_path="json.statements")
    """

    namespace: str
    step_name: str | None
    field_path: str

    @classmethod
    def parse(cls, binding_str: str) -> InputBinding:
        """
        Parse short-form binding string.

        Reserved namespaces: sourceNs, optionsNs, loopNs, mapNs
        Step reference: step_name.field.path

        NEW: Validates balanced brackets for dynamic key syntax.
        """
        # Validate balanced brackets
        if binding_str.count("[") != binding_str.count("]"):
            raise ValueError(f"Unbalanced brackets in binding: {binding_str}")

        # Strip any dynamic key suffix for namespace parsing
        # e.g., "optionsNs.mapping[mapNs.iteration.key]" → "optionsNs.mapping"
        base_str = binding_str.split("[")[0] if "[" in binding_str else binding_str

        parts = base_str.split(".", 1)
        if len(parts) == 1:
            raise ValueError(f"Invalid binding '{binding_str}': missing field path")

        prefix, base_field_path = parts[0], parts[1] if len(parts) > 1 else ""

        # Reconstruct full field_path including dynamic key suffix
        if "[" in binding_str:
            # Extract everything after the first dot
            full_field_path = binding_str.split(".", 1)[1]
        else:
            full_field_path = base_field_path

        if prefix in ("sourceNs", "optionsNs", "loopNs", "mapNs"):
            return cls(namespace=prefix, step_name=None, field_path=full_field_path)
        else:
            # Step reference: prefix is step_name
            return cls(namespace="step", step_name=prefix, field_path=full_field_path)


@dataclass(frozen=True, slots=True)
class OutputBinding:
    """
    Declares expected handler output with optional validation.

    optional=False → Executor errors if handler doesn't return this key
    optional=True → Validation skipped (metadata, conditional outputs)
    """

    binding: InputBinding
    optional: bool = False


@dataclass(slots=True)
class SourceInput:
    """
    Pipeline input data, referenced via sourceNs.* namespace.

    Explicit object replaces implicit source_text parameter.
    """

    text: str
    metadata: dict[str, Any] = dataclass_field(default_factory=dict)

    # Future: Add more source fields as needed (e.g., images, audio)


@dataclass(frozen=True, slots=True)
class CheckpointConfig:
    """
    Checkpoint configuration for pipeline.

    Strategies:
    - per_step: Checkpoint after every step (maximum granularity)
    - milestone: Only checkpoint steps marked checkpoint=milestone
    - none: No checkpointing

    Options:
    - enable_checksums: Compute SHA256 of outputs for integrity (default: False)
    - verify_checksums: Verify on load if checksums present (default: True)
    - version: Pipeline version for schema evolution (default: "1.0")
    """

    enabled: bool = True
    strategy: Literal["per_step", "milestone", "none"] = "per_step"
    backend: str = "filesystem"  # Backend identifier
    storage_path: str = "/tmp/pipeline_checkpoints"
    ttl_seconds: int = 86400  # 24 hours
    resume_on_failure: bool = True
    options: dict = dataclass_field(default_factory=dict)  # Flexible configuration


@dataclass(frozen=True, slots=True)
class MapState:
    """
    Map iteration state accessible via mapNs namespace.

    Only available inside map step during iteration.

    Namespace references:
    - mapNs.iteration.value — Current item (list iteration)
    - mapNs.iteration.key — Current key (dict iteration)
    - mapNs.iteration.index — Current index (0-based)
    - mapNs.iteration.total — Total iteration count
    - mapNs.assigned.model_ref — Pool-assigned model (if configured)
    """

    iteration_index: int
    iteration_value: Any
    iteration_key: str | None  # Only for dict iteration
    iteration_total: int
    assigned_model: str | None = None  # Pool-assigned model

    @property
    def iteration(self) -> MapIterationContext:
        """Return iteration context for namespace access."""
        from .execution.map_reduce import MapIterationContext

        return MapIterationContext(
            index=self.iteration_index,
            value=self.iteration_value,
            key=self.iteration_key,
            total=self.iteration_total,
        )

    @property
    def assigned(self) -> dict[str, str | None]:
        """Pre-computed assignments accessible via mapNs.assigned.*"""
        return {"model_ref": self.assigned_model}


@dataclass(frozen=True, slots=True)
class MapConfig:
    """
    Configuration for map step (fan-out).

    map_over: What to iterate (must resolve to list or dict)
    map_inputs: Inputs that vary per iteration (use mapNs.iteration.*)
    timeout_seconds: Maximum time to wait for all iterations (optional)
    min_success_threshold: Minimum successful iterations required (optional)
        - If int: minimum count (e.g., 3 means "at least 3 must succeed")
        - If float 0.0-1.0: minimum percentage (e.g., 0.6 means "at least 60%")
        - If None: all must succeed (default)
    fail_fast: Stop early when failures make threshold impossible (default: False)
        - Does NOT stop when threshold is met (more results = better)
        - Only stops when failures prove threshold can't be reached
        - Cancels remaining tasks to avoid wasted work
    model_pool: Auto model assignment from pool (optional)
        - Precedence: explicit map_inputs.model_ref > model_pool
    exclude_self: Exclude iteration key from pool candidates (default: False)
    selection: Pool selection strategy (default: "rotate")

    All iterations execute concurrently. System-level concurrency is managed
    by Stargate proxy. Use timeout + threshold for partial success patterns.
    Use fail_fast when retry exhaustion signals futility.
    """

    map_over: dict[str, InputBinding]  # Field -> binding that resolves to list/dict
    map_inputs: dict[str, InputBinding] = dataclass_field(default_factory=dict)
    timeout_seconds: float | None = None
    min_success_threshold: int | float | None = None  # Count or percentage
    fail_fast: bool = False  # Stop when threshold becomes impossible (failures only)
    model_pool: InputBinding | None = None  # Auto model assignment from pool
    exclude_self: bool = False  # Exclude iteration key from candidates
    selection: Literal["random", "rotate", "first"] = "rotate"

    def __post_init__(self) -> None:
        if not self.map_over:
            raise ValueError("map_over must have at least one binding")

        # Validate threshold
        if self.min_success_threshold is not None:
            if isinstance(self.min_success_threshold, float):
                if not 0.0 <= self.min_success_threshold <= 1.0:
                    raise ValueError("Percentage threshold must be between 0.0 and 1.0")
            elif isinstance(self.min_success_threshold, int):
                if self.min_success_threshold < 0:
                    raise ValueError("Count threshold must be non-negative")

        # Validate selection mode
        if self.selection not in ("random", "rotate", "first"):
            raise ValueError(
                f"selection must be 'random', 'rotate', or 'first', "
                f"got {self.selection!r}"
            )


class StepConfig(BaseModel):
    """
    Step configuration for pipeline execution.

    Invariant: ∀ binding ∈ handler_inputs.values(), binding resolved before execute()

    Domain-specific fields captured via extra="allow".

    MAP Step Support:
    - Flat syntax: `type: map` with `map_over` and `map_inputs` at step level
    - Nested syntax: `map_config: { map_over: ..., map_inputs: ... }`
    - Both are normalized to `map_config` via model_validator
    """

    model_config: ConfigDict = ConfigDict(populate_by_name=True, extra="allow")

    # Required
    name: str = Field(alias="id")  # Accepts both "id" and "name" in YAML
    type: str

    # Handler specification (Phase 1)
    handler: str | None = None  # Module path "module:ClassName"
    handler_inputs: dict[str, InputBinding] = Field(default_factory=dict)
    handler_outputs: dict[str, OutputBinding] = Field(default_factory=dict)

    # Common optional fields (from StepSpec)
    model_ref: str | None = None
    prompt_ref: str | None = None
    condition: str | None = None
    skip_token_counting: bool | None = None
    inputs: list[str] | None = None  # Legacy - prefer handler_inputs
    from_: str | None = Field(None, alias="from")
    source_text_id: str | None = None

    # NEW: Explicit generation parameters (matches PromptConfig)
    generation_parameters: dict[str, Any] = Field(default_factory=dict)

    # NEW: Provenance configuration (schema v6)
    provenance_mode: Literal["create", "preserve", "aggregate"] | None = None
    originator_mapping: dict[str, str] | None = None

    # Retry & Timeout (Phase 2)
    retry_policy: dict[str, Any] | None = None  # Parsed to RetryPolicy
    timeout_seconds: float | None = None
    handler_timeout_seconds: float | None = None

    # Checkpoint (Phase 3)
    checkpoint: bool | Literal["milestone"] | None = None

    # Map/Reduce (Phase 4)
    map_config: dict[str, Any] | None = None  # Parsed to MapConfig

    # Pre-resolved inputs from map_inputs (set by MapExecutor for handlers)
    # Used by _build_prompt_context to include iteration-specific template values
    resolved_map_inputs: dict[str, Any] = Field(default_factory=dict)

    # Legacy: explicit depends_on (prefer computed from handler_inputs)
    depends_on: list[str] = Field(default_factory=list)

    @field_validator("type")
    @classmethod
    def reject_map_type(cls, v: str) -> str:
        """
        Reject type='map' - MAP is an execution mode, not a handler type.

        Breaking change: type must always be a real handler type (e.g., 'generate').
        MAP execution is triggered by presence of map_over/map_inputs/map_config.
        """
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
        """
        Normalize flat map_over/map_inputs to nested map_config.

        Supports two YAML syntaxes:
        1. Flat: type: map, map_over: {...}, map_inputs: {...}
        2. Nested: map_config: { map_over: {...}, map_inputs: {...} }

        Converts flat syntax to nested for consistent internal handling.
        """
        if not isinstance(values, dict):
            return values

        # Skip if map_config already set (nested syntax)
        if values.get("map_config") is not None:
            return values

        # Check for flat map_over field
        if "map_over" in values:
            # Build map_config from flat fields
            map_config: dict[str, Any] = {
                "map_over": values.pop("map_over"),
            }
            # Optional map fields
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
            # Note: timeout_seconds stays at step level (used by both map and retry)

            values["map_config"] = map_config

        return values

    @field_validator("handler_inputs", mode="before")
    @classmethod
    def parse_handler_inputs(cls, v: dict[str, Any]) -> dict[str, InputBinding]:
        """Convert string bindings to InputBinding objects.

        Accepts three forms to support round-tripping through model_dump():
        1. String: "step.json.field" → InputBinding.parse(...)
        2. Dict: serialized InputBinding from model_dump() → InputBinding(**dict)
        3. InputBinding: already parsed, pass through
        """
        if not isinstance(v, dict):
            return v

        result = {}
        for key, value in v.items():
            if isinstance(value, str):
                result[key] = InputBinding.parse(value)
            elif isinstance(value, InputBinding):
                result[key] = value
            elif isinstance(value, dict):
                # model_dump(exclude_none=True) serializes InputBinding as a plain dict,
                # omitting step_name=None. Reconstruct explicitly to handle missing key.
                result[key] = InputBinding(
                    namespace=value["namespace"],
                    step_name=value.get("step_name"),
                    field_path=value["field_path"],
                )
            else:
                # Let Pydantic handle the error for invalid types
                result[key] = value
        return result

    @field_validator("handler_outputs", mode="before")
    @classmethod
    def parse_handler_outputs(cls, v: dict[str, Any]) -> dict[str, OutputBinding]:
        """
        Convert string bindings or dict specs to OutputBinding objects.

        Supports four formats to handle round-tripping through model_dump():
        1. String: "step.json.field" → OutputBinding(binding=..., optional=False)
        2. Dict with string binding: {binding: "step.json.field", optional: true}
        3. Dict with serialized InputBinding: {binding: {...}, optional: false}
           — produced by model_dump() in _namespace_step; reconstructed via
           InputBinding(**dict)
        4. OutputBinding: Already parsed
        """
        if not isinstance(v, dict):
            return v

        result = {}
        for key, value in v.items():
            if isinstance(value, str):
                # Format 1: Simple string binding (required by default)
                input_binding = InputBinding.parse(value)
                result[key] = OutputBinding(binding=input_binding)
            elif isinstance(value, dict):
                # Formats 2 & 3: Dict with "binding" and "optional" keys
                binding_val = value.get("binding")
                optional = value.get("optional", False)
                if binding_val is None:
                    raise ValueError(
                        f"handler_outputs[{key!r}]: dict format requires 'binding' key"
                    )
                if isinstance(binding_val, str):
                    input_binding = InputBinding.parse(binding_val)
                elif isinstance(binding_val, dict):
                    # model_dump(exclude_none=True) serializes InputBinding as a plain
                    # dict, omitting step_name=None. Reconstruct explicitly.
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
                # Format 4: Already parsed
                result[key] = value
            else:
                # Let Pydantic handle the error for invalid types
                result[key] = value
        return result

    @model_validator(mode="after")
    def validate_provenance_config(self) -> Self:
        """
        Enforce provenance invariants at schema level.

        Note: originator_mapping is optional for provenance_mode="aggregate".
        Modern handlers (e.g., consensus_combine_v3_3) can resolve originators
        dynamically from MapOutputCollection keys.
        """
        # Validation removed - handlers validate their own requirements
        return self

    @property
    def id(self) -> str:
        """Alias for name - backward compatibility."""
        return self.name

    @property
    def computed_depends_on(self) -> list[str]:
        """
        Derive dependencies from handler_inputs, map_over, config step references,
        and condition expressions.

        Dependency sources:
        1. handler_inputs: step.field bindings (e.g., "decompose_link.*.json.claims")
        2. map_over: collection bindings (e.g., "answer_all.*")
        3. config.*_step: string values where key ends with "_step"
           (e.g., config.claims_step: decompose_link)
        4. condition: step name references in condition expressions
           (e.g., "assess_context.json.get('need_more_history', False) == True")

           YAML structure:
             config:
               claims_step: decompose_link

           Pydantic stores as: model_extra = {'config': {...}}
           Handlers access via: step.config.get('claims_step')
           Dependency extraction checks both model_extra[key] and
           model_extra['config'][key] for *_step keys.

        Invariant: ∀ key ∈ config: key.endswith("_step") ∧ isinstance(val, str)
        ⟹ val ∈ deps
        """
        deps: set[str] = set()

        # Dependencies from handler_inputs
        for binding in self.handler_inputs.values():
            if binding.namespace == "step" and binding.step_name:
                deps.add(binding.step_name)

        # Dependencies from map_over (e.g., answer_all.* references answer_all)
        if self.map_config:
            # map_config is a raw dict, parse bindings inline
            for binding_str in self.map_config.get("map_over", {}).values():
                if isinstance(binding_str, str):
                    binding = InputBinding.parse(binding_str)
                    if binding.namespace == "step" and binding.step_name:
                        deps.add(binding.step_name)

        # Dependencies from config.*_step references (e.g., claims_step: decompose_link)
        # Pydantic stores extra fields in model_extra when extra="allow"
        # Check both top-level model_extra and nested config dict
        if self.model_extra:
            for key, val in self.model_extra.items():
                if key.endswith("_step") and isinstance(val, str):
                    deps.add(val)
                # Check nested config dict for *_step keys
                elif key == "config" and isinstance(val, dict):
                    for config_key, config_val in val.items():
                        if config_key.endswith("_step") and isinstance(config_val, str):
                            deps.add(config_val)

        # Dependencies from condition step references
        if self.condition:
            from .conditions import extract_condition_deps

            deps |= extract_condition_deps(self.condition)

        return list(deps)

    @property
    def is_map_step(self) -> bool:
        """
        Check if this is a map step.

        Map detection is purely based on presence of map fields (map_config),
        NOT based on type. normalize_map_config() ensures flat map_over/map_inputs
        are moved into map_config before this check.

        Invariant: is_map_step ⟺ map_config is not None
        """
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
    ) -> str | None:
        """Get the model_id this step will invoke."""
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
        """
        Parse map_config dict to MapConfig object.

        Handles parsing of string bindings to InputBinding objects.
        """
        if not self.map_config:
            return None

        raw = self.map_config

        # Parse map_over bindings
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

        # Parse map_inputs bindings
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

        # Parse model_pool binding
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

        # Resolve timeout_seconds: map_config dict takes precedence, then step level
        # ∀ map steps: MapExecutor reads timeout from MapConfig, not StepConfig
        timeout_seconds = raw.get("timeout_seconds")
        if timeout_seconds is None:
            # Support flat syntax where timeout_seconds lives at step level
            timeout_seconds = self.timeout_seconds

        return MapConfig(
            map_over=map_over,
            map_inputs=map_inputs,
            timeout_seconds=timeout_seconds,
            min_success_threshold=raw.get("min_success_threshold"),
            fail_fast=raw.get("fail_fast", False),
            model_pool=model_pool,
            exclude_self=raw.get("exclude_self", False),
            selection=raw.get("selection", "rotate"),
        )
