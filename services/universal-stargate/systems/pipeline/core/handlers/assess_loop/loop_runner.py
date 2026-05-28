"""
Assess-loop orchestrator for the assess_loop_v1 handler.

Owns :func:`run_assess_loop` — the full lifecycle that the thin
``AssessLoopHandler.execute`` delegates to. This module is the single owner of
the ``try``/``except``/``finally`` structure that guarantees the critical
lifecycle invariant:

    ∀ run_assess_loop(): AssessLoopStarted ⟹ ∃! AssessLoopCompleted

``AssessLoopStarted`` is emitted before the ``try``; ``AssessLoopCompleted`` is
emitted in the ``finally`` so it fires on every exit path (terminal action,
budget exhaustion, unknown/exit action, parse/handler error, or a re-raised
``ProxyClientError`` / ``ContextExceededError``). Per-iteration phases are
delegated to ``setup`` / ``initial_action`` / ``assess_phase`` / ``action_phase``;
the post-assess decision branching stays inline here because every branch
mutates :class:`LoopState` and emits the iteration-completed event before
breaking — extracting it would buy no SLOC and risk the invariant.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

from universal_logging import get_logger

from ...dag import ContextExceededError
from ...events.assess_loop import AssessLoopCompleted, AssessLoopStarted
from ...execution import ProxyClientError
from ..assess_loop_config import (
    AssessLoopConfig,
    LoopState,
    build_assess_ctx,
    emit_iteration_completed,
)
from ..protocol import StepOutput
from .action_phase import run_action
from .artifact_text import _pre_label_paragraphs
from .assess_phase import run_assess
from .initial_action import run_initial_action
from .setup import resolve_loop_setup

if TYPE_CHECKING:
    from ...schemas import StepConfig
    from ..builtin import BaseHandler
    from ..protocol import PipelineContext

logger = get_logger(__name__)


async def run_assess_loop(
    handler: BaseHandler,
    step: StepConfig,
    context: PipelineContext,
) -> StepOutput:
    """Run the engine-mediated assess→act loop and return its :class:`StepOutput`.

    Lifecycle: resolve setup → emit ``AssessLoopStarted`` → (optional initial
    action) → iterate up to ``cfg.max_iterations`` { assess → decision branch →
    action → progress } → ``finally`` emit ``AssessLoopCompleted`` → assemble
    ``StepOutput``. Loop semantics, exit-reason values, token accounting, and
    artifact mutation rules are byte-for-byte equivalent to the former monolithic
    ``AssessLoopHandler.execute``.
    """
    start_time = time.monotonic()
    cfg: AssessLoopConfig = AssessLoopConfig.from_step(step)

    setup = resolve_loop_setup(handler, step, context, cfg)
    resolved = setup.resolved
    artifact = setup.artifact
    artifact_raw = setup.artifact_raw
    base_ctx = setup.base_ctx
    cached_sys = setup.cached_sys

    recorder = context.recorder
    if recorder:
        recorder.emit(
            AssessLoopStarted(
                step_name=step.name,
                max_iterations=cfg.max_iterations,
                terminal_action=cfg.terminal_action,
                action_names=cfg.action_names,
                has_system_prompt=bool(cfg.system_prompt_ref),
                has_initial_action=bool(cfg.initial_action),
            )
        )

    state = LoopState()

    try:
        # Initial action: generate artifact from scratch when artifact is empty.
        # Skipped when artifact already populated via handler_inputs
        # (step-owned mode).
        if cfg.initial_action and not artifact:
            artifact, artifact_raw = await run_initial_action(
                handler,
                step,
                context,
                cfg,
                base_ctx,
                artifact,
                cached_sys,
                state,
            )

        for iteration in range(cfg.max_iterations):
            assess_artifact = (
                _pre_label_paragraphs(artifact)
                if cfg.pre_label_paragraphs
                else artifact
            )
            assess_ctx = build_assess_ctx(
                base_ctx,
                cfg.artifact_key,
                assess_artifact,
                iteration,
                state.last_decision,
            )
            state.iterations_used = iteration + 1

            outcome = await run_assess(
                handler,
                step,
                context,
                cfg,
                assess_ctx,
                iteration,
                resolved,
                artifact_raw,
                cached_sys,
                state,
            )
            if outcome.exit_reason is not None:
                # assess_handler_error / json_parse_failure — terminating
                # iteration event already emitted inside run_assess.
                state.exit_reason = outcome.exit_reason
                break
            decision = outcome.decision
            assert decision is not None  # exit_reason None ⟹ decision present
            assess_ms = outcome.assess_ms
            iter_pt = outcome.iter_pt
            iter_ct = outcome.iter_ct

            if "artifact" in decision:
                artifact = decision.pop("artifact")
                artifact_raw = artifact
            state.last_decision = decision
            action = decision.get("action", "")
            reason = decision.get("reason", "")
            state.last_action = action

            if action == cfg.terminal_action:
                state.terminal_action_reached = True
                state.exit_reason = "terminal_action"
                emit_iteration_completed(
                    recorder,
                    step.name,
                    iteration,
                    decision,
                    action,
                    reason,
                    True,
                    None,
                    0.0,
                    assess_ms,
                    iter_pt,
                    iter_ct,
                    state=state,
                )
                break

            if action not in cfg.actions:
                logger.warning(
                    "Step '%s' iter %d: unknown action '%s'",
                    step.id,
                    iteration,
                    action,
                )
                emit_iteration_completed(
                    recorder,
                    step.name,
                    iteration,
                    decision,
                    action,
                    reason,
                    False,
                    None,
                    0.0,
                    assess_ms,
                    iter_pt,
                    iter_ct,
                    state=state,
                )
                state.exit_reason = "unknown_action"
                break

            cap = cfg.get_action_max_consecutive(action)
            if state.track_action(action, cap):
                logger.info(
                    "Step '%s' iter %d: action '%s' hit max_consecutive=%d; "
                    "forcing terminal action '%s'",
                    step.id,
                    iteration,
                    action,
                    cap,
                    cfg.terminal_action,
                )
                state.terminal_action_reached = True
                state.exit_reason = "max_consecutive"
                state.last_action = cfg.terminal_action
                emit_iteration_completed(
                    recorder,
                    step.name,
                    iteration,
                    decision,
                    cfg.terminal_action,
                    reason,
                    True,
                    None,
                    0.0,
                    assess_ms,
                    iter_pt,
                    iter_ct,
                    state=state,
                )
                break

            action_outcome = await run_action(
                handler,
                step,
                context,
                cfg,
                base_ctx,
                artifact,
                decision,
                action,
                reason,
                iteration,
                cached_sys,
                state,
            )
            artifact = action_outcome.artifact
            artifact_raw = action_outcome.artifact_raw
            action_model = action_outcome.action_model
            action_ms = action_outcome.action_ms
            action_total_pt = action_outcome.total_pt
            action_total_ct = action_outcome.total_ct

            if action in cfg.exit_actions:
                state.terminal_action_reached = True
                state.exit_reason = "exit_action"
                emit_iteration_completed(
                    recorder,
                    step.name,
                    iteration,
                    decision,
                    action,
                    reason,
                    True,
                    action_model,
                    action_ms,
                    assess_ms,
                    iter_pt + action_total_pt,
                    iter_ct + action_total_ct,
                    state=state,
                )
                break

            emit_iteration_completed(
                recorder,
                step.name,
                iteration,
                decision,
                action,
                reason,
                False,
                action_model,
                action_ms,
                assess_ms,
                iter_pt + action_total_pt,
                iter_ct + action_total_ct,
                state=state,
            )

            handler._report_progress(
                step,
                context,
                items_total=cfg.max_iterations,
                items_completed=iteration + 1,
                models_total=cfg.max_iterations * 2,
                models_completed=state.model_call_count,
            )

    except (ProxyClientError, ContextExceededError):
        # Model API failure or context overflow mid-loop — distinguish from
        # budget exhaustion so the viewer and caller can surface the real cause.
        state.exit_reason = "model_error"
        raise

    finally:
        handler._report_progress(
            step,
            context,
            items_total=cfg.max_iterations,
            items_completed=state.iterations_used,
            models_total=cfg.max_iterations * 2,
            models_completed=state.model_call_count,
        )
        if recorder:
            recorder.emit(
                AssessLoopCompleted(
                    step_name=step.name,
                    iterations_used=state.iterations_used,
                    max_iterations=cfg.max_iterations,
                    terminal_action_reached=state.terminal_action_reached,
                    exit_reason=state.exit_reason,
                    last_action=state.last_action,
                    total_prompt_tokens=state.total_prompt_tokens,
                    total_completion_tokens=state.total_completion_tokens,
                    total_model_calls=state.model_call_count,
                )
            )

    latency_ms = (time.monotonic() - start_time) * 1000
    return StepOutput(
        raw=artifact,
        json={
            "iterations_used": state.iterations_used,
            "exit_reason": state.exit_reason,
            "terminal_action_reached": state.terminal_action_reached,
            "last_assessment": state.last_decision,
            "history": state.history,
        },
        prompt_tokens=state.total_prompt_tokens,
        completion_tokens=state.total_completion_tokens,
        latency_ms=latency_ms,
        model_call_count=state.model_call_count,
    )
