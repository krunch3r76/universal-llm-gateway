"""
Pipeline and step lifecycle events + data capture events.

Lifecycle events track execution progress.
Data capture events record full inputs/outputs for each step,
replacing the execution summary writer.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .base import PipelineEvent

# --- Pipeline lifecycle ---


@dataclass(slots=True, kw_only=True)
class PipelineStarted(PipelineEvent):
    """Emitted when pipeline execution begins."""

    step_count: int = 0
    timeout_seconds: float | None = None
    source_text: str = ""


@dataclass(slots=True, kw_only=True)
class PipelineCompleted(PipelineEvent):
    """Emitted when pipeline completes successfully."""

    duration_ms: float = 0.0
    output_step: str = ""


@dataclass(slots=True, kw_only=True)
class PipelineFailed(PipelineEvent):
    """Emitted when pipeline execution fails."""

    duration_ms: float = 0.0
    error: str = ""
    failed_step: str | None = None
    traceback: str | None = None


@dataclass(slots=True, kw_only=True)
class PipelineCancelled(PipelineEvent):
    """Emitted when pipeline is cancelled (e.g., client disconnect)."""

    duration_ms: float = 0.0
    reason: str = ""
    completed_steps: int = 0
    pending_steps: int = 0


# --- Step lifecycle ---


@dataclass(slots=True, kw_only=True)
class StepStarted(PipelineEvent):
    """Emitted when step execution begins."""

    step_type: str = ""
    is_map_step: bool = False


@dataclass(slots=True, kw_only=True)
class StepCompleted(PipelineEvent):
    """Emitted when step completes successfully."""

    duration_ms: float = 0.0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    model_call_count: int = 0


@dataclass(slots=True, kw_only=True)
class StepFailed(PipelineEvent):
    """Emitted when step execution fails."""

    error: str = ""
    duration_ms: float = 0.0
    traceback: str | None = None
    model_calls: list[dict[str, Any]] | None = None
    prompt_tokens: int = 0
    completion_tokens: int = 0
    model_call_count: int = 0


@dataclass(slots=True, kw_only=True)
class StepProgress(PipelineEvent):
    """Emitted during long-running chunked steps to report progress."""

    items_total: int = 0
    items_completed: int = 0
    models_total: int = 0
    models_completed: int = 0


@dataclass(slots=True, kw_only=True)
class StepSkipped(PipelineEvent):
    """Emitted when step is skipped due to condition evaluation."""

    reason: str = ""


@dataclass(slots=True, kw_only=True)
class StepConditionEvaluated(PipelineEvent):
    """Emitted when a step's condition expression is evaluated."""

    condition: str = ""
    result: bool = False
    available_outputs: list[str] = field(default_factory=list)


# --- Data capture (replaces execution summary writer) ---


@dataclass(slots=True, kw_only=True)
class StepInputsCaptured(PipelineEvent):
    """Full handler inputs for a step, captured before execution."""

    inputs: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True, kw_only=True)
class StepOutputCaptured(PipelineEvent):
    """Full handler output, captured after execution."""

    raw: str = ""
    json_data: dict[str, Any] | None = None
    prompt_tokens: int = 0
    completion_tokens: int = 0
    latency_ms: float = 0.0
    model_call_count: int = 0
    system_prompt: str | None = None
    user_prompt: str | None = None
    request_body: dict[str, Any] | None = None


# --- Map step events ---


@dataclass(slots=True, kw_only=True)
class MapIterationCompleted(PipelineEvent):
    """Emitted when a single map iteration completes."""

    iteration_index: int = 0
    iteration_key: str = ""
    duration_ms: float = 0.0
    output_text: str = ""
    prompt_tokens: int = 0
    completion_tokens: int = 0
