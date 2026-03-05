"""
Configuration and loop utilities for assess_loop_v1 handler.

Parses domain fields from StepConfig into a typed structure and provides
loop utility functions, keeping the handler free of repetitive boilerplate.

Action dispatch supports both single-step (dict) and multi-step (list[dict])
forms. ∀ action: get_action_steps() returns list[ActionStep]; single-step
configs are wrapped for uniform dispatch in the handler loop.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from pipeline_assess_registry import PROGRAMMATIC_ASSESS_HANDLERS
from universal_logging import get_logger

from ..events.assess_loop import AssessLoopIterationCompleted

if TYPE_CHECKING:
    from ..schemas import StepConfig

_logger = get_logger(__name__)

_CITATION_RE = re.compile(r"\[Fact \d+\]")

_RENAMED_FIELDS: dict[str, str] = {
    "context_prompt_ref": "system_prompt_ref",
    "pre_assess_action": "initial_action",
}


@dataclass(slots=True, kw_only=True)
class ActionStep:
    """Single execution step within an action dispatch sequence.

    ∀ action: steps = get_action_steps(action)
    Single-step actions are wrapped in a 1-element list for uniform dispatch.
    """

    prompt_ref: str
    model_ref: str | None = None
    schema: dict[str, Any] | None = None

    @classmethod
    def from_config(cls, d: dict[str, Any]) -> ActionStep:
        rf = d.get("response_format") or {}
        return cls(
            prompt_ref=d["prompt_ref"],
            model_ref=d.get("model_ref"),
            schema=rf.get("schema"),
        )


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
    system_prompt_ref: str | None
    assess_pool: list[str]
    artifact_key: str
    initial_artifact: str | None
    temperature: float | None
    assess_max_tokens: int | None
    assess_handler: str | None
    initial_action: str | None
    strip_xml_tags: list[str]
    pre_label_paragraphs: bool
    # ∀ a ∈ exit_actions: a ∈ actions ∧ after running a the loop exits immediately
    exit_actions: frozenset[str]

    @classmethod
    def from_step(cls, step: StepConfig) -> AssessLoopConfig:
        """Parse AssessLoopConfig from StepConfig domain fields."""
        for old, new in _RENAMED_FIELDS.items():
            if step.get_domain_field(old) is not None:
                raise ValueError(
                    f"Step '{step.id}': '{old}' was renamed to "
                    f"'{new}' — update your YAML"
                )
        return cls(
            assess_schema=step.get_domain_field("assess_schema"),
            actions=step.get_domain_field("actions") or {},
            terminal_action=step.get_domain_field("terminal_action") or "accept",
            max_iterations=step.get_domain_field("max_iterations") or 3,
            system_prompt_ref=step.get_domain_field("system_prompt_ref"),
            assess_pool=step.get_domain_field("assess_pool") or [],
            artifact_key=step.get_domain_field("artifact_key") or "artifact",
            initial_artifact=step.get_domain_field("initial_artifact"),
            temperature=step.generation_parameters.get("temperature"),
            assess_max_tokens=step.generation_parameters.get("max_tokens"),
            assess_handler=step.get_domain_field("assess_handler"),
            initial_action=step.get_domain_field("initial_action"),
            strip_xml_tags=step.get_domain_field("strip_xml_tags") or [],
            pre_label_paragraphs=bool(step.get_domain_field("pre_label_paragraphs")),
            exit_actions=frozenset(step.get_domain_field("exit_actions") or []),
        )

    @property
    def action_names(self) -> list[str]:
        return list(self.actions.keys())

    def get_action_steps(self, action: str) -> list[ActionStep]:
        """Ordered execution steps for a named action.

        Supports both single-step (dict) and multi-step (list[dict]) YAML forms:
          single:  revise: {prompt_ref: ..., model_ref: ...}
          multi:   revise: [{prompt_ref: ...}, {prompt_ref: ...}]

        ∀ action: returns list[ActionStep] with len >= 1.
        Each step runs sequentially; artifact output of step N feeds step N+1.
        """
        cfg = self.actions[action]
        if isinstance(cfg, list):
            return [ActionStep.from_config(s) for s in cfg]
        return [ActionStep.from_config(cfg)]

    def get_action_prompt_ref(self, action: str) -> str:
        """First step's prompt_ref — for single-step callers (e.g. initial_action)."""
        return self.get_action_steps(action)[0].prompt_ref

    def get_action_model_ref(self, action: str, fallback: str) -> str:
        """First step's model_ref or fallback — for single-step callers."""
        return self.get_action_steps(action)[0].model_ref or fallback

    def get_action_schema(self, action: str) -> dict[str, Any] | None:
        """First step's JSON schema — for single-step callers."""
        return self.get_action_steps(action)[0].schema

    def get_action_max_consecutive(self, action: str) -> int | None:
        """Get optional max_consecutive cap for a named action."""
        cfg = self.actions[action]
        cap = cfg.get("max_consecutive") if isinstance(cfg, dict) else None
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
            steps = action_cfg if isinstance(action_cfg, list) else [action_cfg]
            for i, step_cfg in enumerate(steps):
                if not isinstance(step_cfg, dict) or not step_cfg.get("prompt_ref"):
                    label = f"step {i}" if len(steps) > 1 else "step"
                    errors.append(
                        f"Step '{step_id}' action '{action_name}' {label} "
                        "missing prompt_ref"
                    )
            if isinstance(action_cfg, dict):
                cap = action_cfg.get("max_consecutive")
                if cap is not None and (not isinstance(cap, int) or cap < 1):
                    errors.append(
                        f"Step '{step_id}' action '{action_name}' has invalid "
                        "max_consecutive (must be integer >= 1)"
                    )
        if (
            self.assess_handler is not None
            and self.assess_handler not in PROGRAMMATIC_ASSESS_HANDLERS
        ):
            errors.append(
                f"Step '{step_id}' assess_handler '{self.assess_handler}' is not registered "
                "(ensure the handler module is imported before pipeline load)"
            )
        if self.initial_action is not None and self.initial_action not in self.actions:
            errors.append(
                f"Step '{step_id}' initial_action '{self.initial_action}' "
                "not defined in actions"
            )
        unknown_exits = self.exit_actions - set(self.actions)
        if unknown_exits:
            errors.append(
                f"Step '{step_id}' exit_actions {sorted(unknown_exits)} "
                "not defined in actions"
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


def sanitize_repeated_items_decision(
    decision: dict[str, Any],
    terminal_action: str,
    step_id: str,
) -> dict[str, Any]:
    """Strip hallucinated and citation-unsafe pairs from a redundancy decision.

    Applied when the assessor returns a `repeated_items` list (redundancy steps).
    Leaves all other decision shapes unchanged.

    Invariants:
    - ∀ pair: kept.strip() == deleted.strip() ⟹ stripped (self-referential hallucination)
    - ∀ pair: ∃ [Fact N] ∈ deleted ∧ [Fact N] ∉ kept ⟹ stripped (unique knowledge)
    - stripped_all ⟹ action = terminal_action, target = "" (short-circuit reviser)
    - some_stripped ⟹ target rebuilt from valid pairs only
    """
    repeated_items = decision.get("repeated_items")
    if not isinstance(repeated_items, list) or not repeated_items:
        return decision

    valid: list[dict[str, Any]] = []
    n_self_ref = 0
    n_unique_cite = 0

    for pair in repeated_items:
        if not isinstance(pair, dict):
            continue
        kept = str(pair.get("kept", "")).strip()
        deleted = str(pair.get("deleted", "")).strip()

        if kept == deleted:
            n_self_ref += 1
            continue

        kept_cites = set(_CITATION_RE.findall(kept))
        deleted_cites = set(_CITATION_RE.findall(deleted))
        if not deleted_cites.issubset(kept_cites):
            n_unique_cite += 1
            continue

        valid.append(pair)

    total_stripped = n_self_ref + n_unique_cite
    if total_stripped == 0:
        return decision

    _logger.warning(
        "Step '%s': stripped %d invalid repeated_items pair(s) "
        "(self-referential=%d, unique-citation=%d); %d valid pair(s) remain",
        step_id,
        total_stripped,
        n_self_ref,
        n_unique_cite,
        len(valid),
    )

    result = {**decision}
    if not valid:
        result["action"] = terminal_action
        result["repeated_items"] = []
        result["target"] = ""
        suffix = " [handler: all proposed pairs invalid — forced accept]"
        result["reason"] = (decision.get("reason", "") + suffix).strip()
    else:
        result["repeated_items"] = valid
        deletions = "\n".join(f"- {p['deleted']}" for p in valid)
        result["target"] = f"Delete the following sentences:\n{deletions}"

    return result


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
