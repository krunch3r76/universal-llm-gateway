"""
Shared step-related data types.

These types are used by step configuration and execution paths.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from dataclasses import field as dataclass_field
from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:
    from .execution.map_reduce import MapIterationContext


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

        parts = []
        for k, v in sorted(vars(self).items()):
            if isinstance(v, str | int | float | bool | type(None)):
                parts.append(f"{k}={v}")
            else:
                parts.append(f"{k}:{type(v).__name__}")

        return hashlib.sha256(":".join(parts).encode()).hexdigest()[:16]


@dataclass
class StepOutput:
    """
    Output from an executed step.

    Supports both dict-style and attribute access for traverse_path compatibility.
    """

    data: dict[str, Any]
    metadata: dict[str, Any] = dataclass_field(default_factory=dict)

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
            return json.dumps(self.data)
        return str(self.data)

    def to_checkpoint_json(self) -> dict | None:
        """Extract JSON data for checkpointing."""
        return self.data if isinstance(self.data, dict) else None

    def to_checkpoint_data(self) -> tuple[str, dict | None, dict]:
        """Extract checkpoint data from output."""
        return (self.to_checkpoint_raw(), self.to_checkpoint_json(), self.metadata)


@dataclass(frozen=True, slots=True)
class InputBinding:
    """
    Binds a handler input field to a data source.

    Invariant:
        namespace ∈ {"sourceNs", "optionsNs", "loopNs", "mapNs"}
        ∨ (namespace == "step" ∧ step_name ≠ None)
    """

    namespace: str
    step_name: str | None
    field_path: str

    @classmethod
    def parse(cls, binding_str: str) -> InputBinding:
        """Parse short-form binding string."""
        if binding_str.count("[") != binding_str.count("]"):
            raise ValueError(f"Unbalanced brackets in binding: {binding_str}")

        base_str = binding_str.split("[")[0] if "[" in binding_str else binding_str
        parts = base_str.split(".", 1)
        if len(parts) == 1:
            raise ValueError(f"Invalid binding '{binding_str}': missing field path")

        prefix, base_field_path = parts[0], parts[1] if len(parts) > 1 else ""
        if "[" in binding_str:
            full_field_path = binding_str.split(".", 1)[1]
        else:
            full_field_path = base_field_path

        if prefix in ("sourceNs", "optionsNs", "loopNs", "mapNs"):
            return cls(namespace=prefix, step_name=None, field_path=full_field_path)
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

    For chat completions, `messages` holds the full conversation history
    and `text` holds the last user message content (backward compat).
    """

    text: str
    metadata: dict[str, Any] = dataclass_field(default_factory=dict)
    messages: list[dict[str, Any]] | None = None


@dataclass(frozen=True, slots=True)
class CheckpointConfig:
    """Checkpoint configuration for pipeline."""

    enabled: bool = True
    strategy: Literal["per_step", "milestone", "none"] = "per_step"
    backend: str = "filesystem"
    storage_path: str = "/tmp/pipeline_checkpoints"
    ttl_seconds: int = 86400
    resume_on_failure: bool = True
    options: dict = dataclass_field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class MapState:
    """Map iteration state accessible via mapNs namespace."""

    iteration_index: int
    iteration_value: Any
    iteration_key: str | None
    iteration_total: int
    assigned_model: str | None = None

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
    """Configuration for map step (fan-out)."""

    map_over: dict[str, InputBinding]
    map_inputs: dict[str, InputBinding] = dataclass_field(default_factory=dict)
    timeout_seconds: float | None = None
    min_success_threshold: int | float | None = None
    fail_fast: bool = False
    model_pool: InputBinding | None = None
    model_requirements: dict[str, Any] | None = None
    exclude_self: bool = False
    selection: Literal["random", "rotate", "first"] = "rotate"

    def __post_init__(self) -> None:
        if not self.map_over:
            raise ValueError("map_over must have at least one binding")

        if self.min_success_threshold is not None:
            if isinstance(self.min_success_threshold, float):
                if not 0.0 <= self.min_success_threshold <= 1.0:
                    raise ValueError("Percentage threshold must be between 0.0 and 1.0")
            elif isinstance(self.min_success_threshold, int):
                if self.min_success_threshold < 0:
                    raise ValueError("Count threshold must be non-negative")

        if self.selection not in ("random", "rotate", "first"):
            raise ValueError(
                f"selection must be 'random', 'rotate', or 'first', "
                f"got {self.selection!r}"
            )

