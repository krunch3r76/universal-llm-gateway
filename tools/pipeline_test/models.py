"""Pure dataclasses for pipeline testing infrastructure.

No I/O, no imports beyond stdlib. These are the shared vocabulary
between snapshot, replay, and compare services.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True, kw_only=True)
class ModelCall:
    """A single model invocation within a step (or assess_loop iteration)."""

    call_label: str
    model_id: str
    system_prompt: str
    user_prompt: str
    request_body: dict[str, Any]
    response_text: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    latency_ms: float = 0.0
    inference_ms: float = 0.0


@dataclass(slots=True, kw_only=True)
class StepSnapshot:
    """Complete snapshot of a single pipeline step execution."""

    step_name: str
    step_type: str
    model_id: str | None = None
    inputs: dict[str, Any] = field(default_factory=dict)
    input_sources: dict[str, str] = field(default_factory=dict)
    raw_output: str = ""
    json_output: dict[str, Any] | None = None
    model_calls: list[ModelCall] = field(default_factory=list)
    duration_ms: float = 0.0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    model_call_count: int = 0
    skipped: bool = False
    skip_reason: str | None = None
    loop_config: dict[str, Any] | None = None
    loop_iterations: list[dict[str, Any]] | None = None


@dataclass(slots=True, kw_only=True)
class ExecutionSnapshot:
    """Full pipeline execution captured from events.jsonl."""

    pipeline_id: str
    execution_id: str
    source_dir: str
    wall_clock: str
    source_text: str = ""
    steps: dict[str, StepSnapshot] = field(default_factory=dict)
    step_order: list[str] = field(default_factory=list)
    total_duration_ms: float = 0.0


@dataclass(slots=True, kw_only=True)
class ReplayOverrides:
    """Overrides for step replay — model, params, prompt."""

    model: str | None = None
    temperature: float | None = None
    max_tokens: int | None = None
    prompt_ref: str | None = None
    extra_params: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True, kw_only=True)
class ReplayResult:
    """Result of replaying a single model call."""

    step_name: str
    call_label: str | None = None
    model_id: str = ""
    response_text: str = ""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    latency_ms: float = 0.0
    request_body: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True, kw_only=True)
class ComparisonResult:
    """Diff between original and replayed output."""

    step_name: str
    call_label: str | None = None
    original_text: str = ""
    replay_text: str = ""
    unified_diff: str = ""
    length_delta: int = 0
    token_delta: dict[str, int] = field(default_factory=dict)


@dataclass(slots=True, kw_only=True)
class StepConfigMatch:
    """A step definition resolved from pipeline YAML.

    Carries both the parent pipeline config (needed for optionsNs.* resolution)
    and the individual step config dict.
    """

    pipeline_config: dict[str, Any]
    step_config: dict[str, Any]


@dataclass(slots=True, kw_only=True)
class ConsultResult:
    """Response from a consultant model evaluating a pipeline step."""

    model_id: str
    response_text: str = ""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    latency_ms: float = 0.0
    error: str | None = None
