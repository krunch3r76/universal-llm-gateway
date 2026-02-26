"""
Configuration and loop utilities for assess_loop_v1 handler.

Parses domain fields from StepConfig into a typed structure and provides
loop utility functions, keeping the handler free of repetitive boilerplate.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from systems.pipeline.core.events.assess_loop import AssessLoopIterationCompleted

if TYPE_CHECKING:
    from systems.pipeline.core.schemas import StepConfig


@dataclass(slots=True, kw_only=True)
class AssessLoopConfig:
    """
    Typed configuration for a single assess_loop_v1 step execution.

    Invariant: ∀ instance: terminal_action ≠ "" ∧ max_iterations > 0
    """

    assess_schema: dict[str, Any] | None
    actions: dict[str, Any]
    terminal_action: str
    max_iterations: int
    context_prompt_ref: str | None
    assess_pool: list[str]
    artifact_key: str
    temperature: float | None
    assess_max_tokens: int | None

    @classmethod
    def from_step(cls, step: StepConfig) -> AssessLoopConfig:
        """Parse AssessLoopConfig from StepConfig domain fields."""
        return cls(
            assess_schema=step.get_domain_field("assess_schema"),
            actions=step.get_domain_field("actions") or {},
            terminal_action=step.get_domain_field("terminal_action") or "accept",
            max_iterations=step.get_domain_field("max_iterations") or 3,
            context_prompt_ref=step.get_domain_field("context_prompt_ref"),
            assess_pool=step.get_domain_field("assess_pool") or [],
            artifact_key=step.get_domain_field("artifact_key") or "artifact",
            temperature=step.generation_parameters.get("temperature"),
            assess_max_tokens=step.generation_parameters.get("max_tokens"),
        )

    @property
    def action_names(self) -> list[str]:
        return list(self.actions.keys())

    def get_action_prompt_ref(self, action: str) -> str:
        """Get prompt_ref for a named action. Raises KeyError if unknown."""
        return self.actions[action]["prompt_ref"]

    def get_action_model_ref(self, action: str, fallback: str) -> str:
        """Get model_ref for a named action, falling back to step model_ref."""
        return self.actions[action].get("model_ref") or fallback

    def get_action_max_consecutive(self, action: str) -> int | None:
        """Get optional max_consecutive cap for a named action."""
        cap = self.actions[action].get("max_consecutive")
        if cap is None:
            return None
        return int(cap)

    def get_assess_model_ref(self, iteration: int, step_model_ref: str) -> str:
        """Get model_ref for an assess call, rotating pool round-robin if set."""
        if self.assess_pool:
            return self.assess_pool[iteration % len(self.assess_pool)]
        return step_model_ref

    def validate(self, step_id: str) -> list[str]:
        """Return validation errors for this config."""
        errors: list[str] = []
        if not self.terminal_action:
            errors.append(f"Step '{step_id}' missing terminal_action")
        if not self.actions:
            errors.append(f"Step '{step_id}' missing actions")
        for action_name, action_cfg in self.actions.items():
            if not isinstance(action_cfg, dict) or not action_cfg.get("prompt_ref"):
                errors.append(
                    f"Step '{step_id}' action '{action_name}' missing prompt_ref"
                )
                continue
            cap = action_cfg.get("max_consecutive")
            if cap is None:
                continue
            if not isinstance(cap, int) or cap < 1:
                errors.append(
                    f"Step '{step_id}' action '{action_name}' has invalid "
                    "max_consecutive (must be integer >= 1)"
                )
        return errors


@dataclass
class LoopState:
    """Mutable loop state shared between loop body and finally block."""

    iterations_used: int = 0
    terminal_action_reached: bool = False
    exit_reason: str = "budget_exhausted"
    last_action: str = ""
    last_decision: dict[str, Any] | None = None
    total_prompt_tokens: int = 0
    total_completion_tokens: int = 0
    model_call_count: int = 0
    consecutive_action_count: int = 0
    consecutive_action_name: str = ""
    history: list[dict[str, Any]] = field(default_factory=list)

    def add_tokens(self, prompt: int, completion: int) -> None:
        self.total_prompt_tokens += prompt
        self.total_completion_tokens += completion
        self.model_call_count += 1

    def track_action(self, action: str, cap: int | None = None) -> bool:
        """Track consecutive repeats and report whether the optional cap is exceeded."""
        if action == self.consecutive_action_name:
            self.consecutive_action_count += 1
        else:
            self.consecutive_action_name = action
            self.consecutive_action_count = 1
        return cap is not None and self.consecutive_action_count > cap


def build_assess_ctx(
    base_ctx: dict[str, Any],
    artifact_key: str,
    artifact: str,
    iteration: int,
    last_decision: dict[str, Any] | None,
) -> dict[str, Any]:
    """Build per-iteration context for assess prompt rendering."""
    ctx = {**base_ctx, artifact_key: artifact, "iteration": iteration}
    if last_decision:
        ctx.update(
            {
                "assess_action": last_decision.get("action", ""),
                "assess_target": last_decision.get("target", ""),
                "assess_reason": last_decision.get("reason", ""),
            }
        )
    return ctx


def emit_iteration_completed(
    recorder: Any,
    step_name: str,
    iteration: int,
    decision: dict[str, Any] | None,
    action: str,
    reason: str,
    is_terminal: bool,
    action_model_id: str | None,
    action_latency_ms: float,
    assess_latency_ms: float,
    iter_pt: int,
    iter_ct: int,
    *,
    state: LoopState | None = None,
) -> None:
    """Emit AssessLoopIterationCompleted and optionally track history."""
    if recorder:
        recorder.emit(
            AssessLoopIterationCompleted(
                step_name=step_name,
                iteration=iteration,
                decision=decision,
                action=action,
                reason=reason,
                is_terminal=is_terminal,
                action_model_id=action_model_id,
                action_latency_ms=action_latency_ms,
                assess_latency_ms=assess_latency_ms,
                iteration_prompt_tokens=iter_pt,
                iteration_completion_tokens=iter_ct,
            )
        )
    if state is not None:
        state.history.append(
            {
                "iteration": iteration,
                "action": action,
                "reason": reason,
                "is_terminal": is_terminal,
                "model": action_model_id or "",
                "assess_latency_ms": round(assess_latency_ms, 1),
                "action_latency_ms": round(action_latency_ms, 1),
                "prompt_tokens": iter_pt,
                "completion_tokens": iter_ct,
            }
        )
