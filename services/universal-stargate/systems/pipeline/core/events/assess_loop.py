"""
Assess loop lifecycle events.

Emitted by AssessLoopHandler to capture iteration semantics not visible
in ModelInvocation events alone (loop bounds, decision rationale, termination cause).

Invariants:
- ∀ AssessLoopStarted ⟹ ∃! AssessLoopCompleted (even on error — handler catches)
- AssessLoopIterationCompleted count ∈ [0, max_iterations]
- ∀ AssessLoopCompleted.exit_reason ∈ {terminal_action, budget_exhausted,
  json_parse_failure, unknown_action, model_error, assess_handler_error}
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .base import PipelineEvent


@dataclass(slots=True, kw_only=True)
class AssessLoopStarted(PipelineEvent):
    """Emitted once before the first iteration.

    Gives the UI loop configuration so it can render a progress indicator
    with known bounds.
    """

    max_iterations: int = 0
    terminal_action: str = ""
    action_names: list[str] | None = None  # configured action keys
    has_system_prompt: bool = False
    has_initial_action: bool = False


@dataclass(slots=True, kw_only=True)
class AssessLoopInitialActionCompleted(PipelineEvent):
    """Emitted after the initial model call, before iteration 0.

    The initial call generates the artifact from scratch (initial_action).
    It is outside the iteration budget and always labeled "initial".
    """

    action: str = ""  # the action name used (e.g. "synthesize")
    model_id: str = ""  # resolved model ID
    latency_ms: float = 0.0
    prompt_tokens: int = 0
    completion_tokens: int = 0


@dataclass(slots=True, kw_only=True)
class AssessLoopIterationCompleted(PipelineEvent):
    """Emitted after each assess→decide→dispatch cycle.

    This is the primary event the UI uses to render per-iteration detail:
    what the model decided, why, and what happened.
    """

    iteration: int = 0  # 0-indexed
    decision: dict[str, Any] | None = None  # full parsed JSON from assessor
    action: str = ""  # selected action (or terminal_action)
    reason: str = ""  # assessor's reason field
    is_terminal: bool = False  # True if action == terminal_action
    action_model_id: str | None = None  # model used for action (None if terminal)
    action_latency_ms: float = 0.0  # action call latency (0 if terminal)
    assess_latency_ms: float = 0.0  # assessment call latency
    iteration_prompt_tokens: int = 0  # tokens for this iteration (assess + action)
    iteration_completion_tokens: int = 0


@dataclass(slots=True, kw_only=True)
class AssessLoopCompleted(PipelineEvent):
    """Emitted when assess_loop_v1 exits its iteration cycle.

    Gives the UI a summary and the termination cause, which is a
    first-class concept (not inferrable from ModelInvocation events).

    total_model_calls includes the pre-assess call when present.
    """

    iterations_used: int = 0
    max_iterations: int = 0
    terminal_action_reached: bool = False
    # terminal_action | budget_exhausted | json_parse_failure | unknown_action
    # model_error | assess_handler_error | max_consecutive
    exit_reason: str = ""
    last_action: str = ""  # last action taken (or terminal action)
    total_prompt_tokens: int = 0
    total_completion_tokens: int = 0
    total_model_calls: int = 0  # assess + action calls combined
