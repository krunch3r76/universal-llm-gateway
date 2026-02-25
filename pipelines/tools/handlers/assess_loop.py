"""
Engine-mediated iterative loop handler (assess_loop_v1).

Calls a model to assess the current state, interprets its structured JSON
decision, dispatches the chosen action (model call with a different prompt),
accumulates the result, and repeats until the model signals completion or
budget is exhausted.

Invariants:
- ∀ execute(): AssessLoopStarted ⟹ ∃! AssessLoopCompleted (finally block)
- ∀ action call: returns plain text → becomes new artifact for next iteration
- ∀ assess call: returns JSON matching assess_schema
- artifact_key identifies which handler_input is the mutable artifact
"""

from __future__ import annotations

import json
import time
from typing import TYPE_CHECKING, Any, override

from systems.pipeline.core.events.assess_loop import (
    AssessLoopCompleted,
    AssessLoopStarted,
)
from systems.pipeline.core.execution import ProxyClientError
from systems.pipeline.core.execution.resolver import NamespaceResolver
from systems.pipeline.core.handlers.builtin import BaseHandler
from systems.pipeline.core.handlers.protocol import StepOutput
from universal_logging import get_logger

from .assess_loop_config import (
    AssessLoopConfig,
    LoopState,
    build_assess_ctx,
    emit_iteration_completed,
)

if TYPE_CHECKING:
    from systems.pipeline.core.handlers.protocol import PipelineContext
    from systems.pipeline.core.schemas import StepConfig

logger = get_logger(__name__)


class AssessLoopHandler(BaseHandler):
    """
    Engine-mediated iterative assess→act loop.

    Each iteration the assessor model returns a structured JSON decision;
    the engine dispatches the named action (a separate model call) or
    exits when the terminal action is reached or the budget is exhausted.

    Key property: works with any instruction-following model that produces
    reliable structured output. No tool-calling training required — the
    model makes decisions via JSON, the engine dispatches.

    See assess_loop_config.py for YAML field documentation.
    """

    step_type: str = "assess_loop_v1"

    @override
    async def execute(
        self,
        step: StepConfig,
        context: PipelineContext,
    ) -> StepOutput:
        start_time = time.monotonic()
        cfg = AssessLoopConfig.from_step(step)

        # Resolve handler_inputs
        resolver = NamespaceResolver(context)
        resolved: dict[str, Any] = {
            name: self._resolve_input(resolver, step, name, step.handler_inputs)
            for name in step.handler_inputs
        }

        if cfg.artifact_key not in resolved:
            raise ValueError(
                f"Step '{step.id}': artifact_key '{cfg.artifact_key}' not in "
                f"handler_inputs. Available: {list(resolved.keys())}"
            )
        artifact: str = str(resolved[cfg.artifact_key])
        base_ctx: dict[str, Any] = {"text": context.source_text, **resolved}

        # Render static context ONCE (outside loop) → system_prompt for all calls,
        # enabling vLLM prefix cache reuse across every assess and action call.
        # The context prompt's rendered *template* (user_prompt) is repurposed as
        # the shared system prompt — a stable KV-cache prefix across all iterations.
        cached_sys: str | None = None
        if cfg.context_prompt_ref:
            static = {k: v for k, v in base_ctx.items() if k != cfg.artifact_key}
            cached_sys = self._render_prompt(
                cfg.context_prompt_ref, static, context
            ).user_prompt

        recorder = context.recorder
        if recorder:
            recorder.emit(
                AssessLoopStarted(
                    step_name=step.name,
                    max_iterations=cfg.max_iterations,
                    terminal_action=cfg.terminal_action,
                    action_names=cfg.action_names,
                    has_context_prompt=bool(cfg.context_prompt_ref),
                )
            )

        state = LoopState()

        try:
            for iteration in range(cfg.max_iterations):
                assess_ctx = build_assess_ctx(
                    base_ctx, cfg.artifact_key, artifact, iteration, state.last_decision
                )
                assess_model = self._resolve_model_alias(
                    cfg.get_assess_model_ref(iteration, step.model_ref), context
                )
                rendered = self._render_prompt(step.prompt_ref, assess_ctx, context)
                sys_p = cached_sys or rendered.system_prompt
                t0 = time.monotonic()

                assess_r = await self._call_model(
                    assess_model,
                    rendered.user_prompt,
                    step,
                    context,
                    sys_p,
                    temperature=cfg.temperature,
                    max_tokens=cfg.assess_max_tokens,
                    json_schema=cfg.assess_schema,
                    call_label=f"assess_{iteration}",
                )
                assess_ms = (time.monotonic() - t0) * 1000
                state.iterations_used = iteration + 1
                state.add_tokens(assess_r.prompt_tokens, assess_r.completion_tokens)

                try:
                    decision: dict[str, Any] = json.loads(assess_r.content)
                except json.JSONDecodeError as exc:
                    logger.warning(
                        "Step '%s' iter %d: JSON parse failure: %s",
                        step.id,
                        iteration,
                        exc,
                    )
                    emit_iteration_completed(
                        recorder,
                        step.name,
                        iteration,
                        None,
                        "",
                        "",
                        False,
                        None,
                        0.0,
                        assess_ms,
                        assess_r.prompt_tokens,
                        assess_r.completion_tokens,
                        state=state,
                    )
                    state.exit_reason = "json_parse_failure"
                    break

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
                        assess_r.prompt_tokens,
                        assess_r.completion_tokens,
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
                        assess_r.prompt_tokens,
                        assess_r.completion_tokens,
                        state=state,
                    )
                    state.exit_reason = "unknown_action"
                    break

                action_ctx = {
                    **base_ctx,
                    cfg.artifact_key: artifact,
                    "assess_action": action,
                    "assess_target": decision.get("target", ""),
                    "assess_reason": reason,
                }
                action_rendered = self._render_prompt(
                    cfg.get_action_prompt_ref(action), action_ctx, context
                )
                action_model = self._resolve_model_alias(
                    cfg.get_action_model_ref(action, step.model_ref), context
                )
                t1 = time.monotonic()
                action_r = await self._call_model(
                    action_model,
                    action_rendered.user_prompt,
                    step,
                    context,
                    cached_sys or action_rendered.system_prompt,
                    temperature=cfg.temperature,
                    call_label=f"action_{action}_{iteration}",
                )
                action_ms = (time.monotonic() - t1) * 1000
                state.add_tokens(action_r.prompt_tokens, action_r.completion_tokens)
                artifact = action_r.content.strip()

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
                    assess_r.prompt_tokens + action_r.prompt_tokens,
                    assess_r.completion_tokens + action_r.completion_tokens,
                    state=state,
                )

                self._report_progress(
                    step,
                    context,
                    items_total=cfg.max_iterations,
                    items_completed=iteration + 1,
                    models_total=cfg.max_iterations * 2,
                    models_completed=state.model_call_count,
                )

        except ProxyClientError:
            # Model API failure mid-loop — distinguish from budget exhaustion so
            # the viewer and caller can surface the real cause.
            state.exit_reason = "model_error"
            raise

        finally:
            self._report_progress(
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

    @override
    def validate(self, step: StepConfig) -> list[str]:
        errors = []
        if not step.model_ref:
            errors.append(f"Step '{step.id}' missing model_ref")
        if not step.prompt_ref:
            errors.append(f"Step '{step.id}' missing prompt_ref")
        if not step.handler_inputs:
            errors.append(f"Step '{step.id}' missing handler_inputs")
        errors.extend(AssessLoopConfig.from_step(step).validate(step.id))
        return errors
