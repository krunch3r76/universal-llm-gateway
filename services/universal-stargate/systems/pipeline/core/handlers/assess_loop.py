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
- ∀ programmatic handler returning "artifact" key: value replaces loop artifact
  (popped before storing in last_decision to avoid bloating history)
"""

from __future__ import annotations

import json
import re
import time
from typing import TYPE_CHECKING, Any, override

from universal_logging import get_logger

from ..events.assess_loop import (
    AssessLoopCompleted,
    AssessLoopInitialActionCompleted,
    AssessLoopStarted,
)
from ..execution import ProxyClientError
from ..execution.resolver import NamespaceResolver
from .assess_loop_config import (
    PROGRAMMATIC_ASSESS_HANDLERS,
    AssessLoopConfig,
    LoopState,
    build_assess_ctx,
    emit_iteration_completed,
    sanitize_repeated_items_decision,
)
from .builtin import BaseHandler
from .protocol import StepOutput
from .registry import register_handler

if TYPE_CHECKING:
    from .protocol import PipelineContext
    from ..schemas import StepConfig

logger = get_logger(__name__)


def _format_text_list(value: Any) -> Any:
    """Compress a list of dicts with a 'text' field into a numbered plain-text list.

    ∀ value: list[dict] ∧ "text" ∈ value[0] ⟹ "[1] text\n[2] text\n…"
    ∀ other value: returned unchanged.
    """
    if (
        isinstance(value, list)
        and value
        and isinstance(value[0], dict)
        and "text" in value[0]
    ):
        return "\n".join(
            f"[{i}] {item['text']}"
            for i, item in enumerate(value, 1)
            if item.get("text")
        )
    return value


def _strip_xml_tags(text: str, tags: list[str]) -> str:
    """Remove named XML blocks from text (e.g. <reasoning>...</reasoning>).

    ∀ tag ∈ tags: all occurrences stripped, including multiline content.
    Applied after each model response so downstream LLM calls and the final
    artifact are free of bookkeeping blocks the model appended for the assessor.
    """
    for tag in tags:
        text = re.sub(rf"<{re.escape(tag)}>.*?</{re.escape(tag)}>", "", text, flags=re.DOTALL | re.IGNORECASE)
    return text.strip()


@register_handler
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
            if cfg.initial_artifact is not None:
                # Seed the artifact from the literal initial_artifact domain field
                # (e.g. "" to bootstrap a coverage-check loop without a prior step).
                # The key is injected into resolved so downstream contexts stay consistent.
                resolved[cfg.artifact_key] = cfg.initial_artifact
            else:
                raise ValueError(
                    f"Step '{step.id}': artifact_key '{cfg.artifact_key}' not in "
                    f"handler_inputs and no initial_artifact provided. "
                    f"Available: {list(resolved.keys())}"
                )

        # Compress list-of-dict inputs that carry a "text" field into numbered
        # plain-text lists before they hit the prompt template. Raw JSON reprs
        # of structured fact objects balloon token usage with fields (statement_id,
        # source_sentences, claim_type, …) that are irrelevant to assess/act calls.
        # The artifact is excluded — it is always a plain string from a prior step.
        resolved = {
            k: _format_text_list(v) if k != cfg.artifact_key else v
            for k, v in resolved.items()
        }

        artifact: str = str(resolved[cfg.artifact_key])
        artifact_raw: str = artifact  # unstripped; used by programmatic assess handlers

        base_ctx: dict[str, Any] = {"text": context.source_text, **resolved}

        # Render system_prompt_ref ONCE (outside loop) — stable KV-cache prefix
        # reused as the system prompt for every assess and action call.
        cached_sys: str | None = None
        if cfg.system_prompt_ref:
            static = {k: v for k, v in base_ctx.items() if k != cfg.artifact_key}
            cached_sys = self._render_prompt(
                cfg.system_prompt_ref, static, context
            ).user_prompt

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
            # Skipped when artifact already populated via handler_inputs (step-owned mode).
            if cfg.initial_action and not artifact:
                initial_ctx = {**base_ctx, cfg.artifact_key: artifact}
                initial_rendered = self._render_prompt(
                    cfg.get_action_prompt_ref(cfg.initial_action), initial_ctx, context
                )
                initial_model = self._resolve_model_alias(
                    cfg.get_action_model_ref(cfg.initial_action, step.model_ref), context
                )
                t_pre = time.monotonic()
                pre_r = await self._call_model(
                    initial_model,
                    initial_rendered.user_prompt,
                    step,
                    context,
                    cached_sys or initial_rendered.system_prompt,
                    temperature=cfg.temperature,
                    call_label="initial",
                )
                pre_latency_ms = (time.monotonic() - t_pre) * 1000
                artifact_raw = pre_r.content.strip()
                artifact = _strip_xml_tags(artifact_raw, cfg.strip_xml_tags)
                state.add_tokens(pre_r.prompt_tokens, pre_r.completion_tokens)
                if recorder:
                    recorder.emit(
                        AssessLoopInitialActionCompleted(
                            step_name=step.name,
                            action=cfg.initial_action,
                            model_id=initial_model,
                            latency_ms=pre_latency_ms,
                            prompt_tokens=pre_r.prompt_tokens,
                            completion_tokens=pre_r.completion_tokens,
                        )
                    )

            for iteration in range(cfg.max_iterations):
                assess_ctx = build_assess_ctx(
                    base_ctx, cfg.artifact_key, artifact, iteration, state.last_decision
                )
                state.iterations_used = iteration + 1

                if cfg.assess_handler is not None:
                    # Programmatic assess — call registered Python function, no LLM.
                    handler_fn = PROGRAMMATIC_ASSESS_HANDLERS[cfg.assess_handler]
                    handler_input = {**resolved, cfg.artifact_key: artifact_raw}
                    t0 = time.monotonic()
                    try:
                        decision: dict[str, Any] = handler_fn(handler_input)
                    except Exception as exc:
                        logger.error(
                            "Step '%s' iter %d: assess_handler '%s' raised: %s",
                            step.id,
                            iteration,
                            cfg.assess_handler,
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
                            (time.monotonic() - t0) * 1000,
                            0,
                            0,
                            state=state,
                        )
                        state.exit_reason = "assess_handler_error"
                        break
                    assess_ms = (time.monotonic() - t0) * 1000
                    iter_pt = 0
                    iter_ct = 0
                else:
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
                    state.add_tokens(assess_r.prompt_tokens, assess_r.completion_tokens)
                    iter_pt = assess_r.prompt_tokens
                    iter_ct = assess_r.completion_tokens

                    try:
                        decision = json.loads(assess_r.content)
                        decision = sanitize_repeated_items_decision(
                            decision, cfg.terminal_action, step.id
                        )
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
                            iter_pt,
                            iter_ct,
                            state=state,
                        )
                        state.exit_reason = "json_parse_failure"
                        break

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
                artifact_raw = action_r.content.strip()
                artifact = _strip_xml_tags(artifact_raw, cfg.strip_xml_tags)

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
                    iter_pt + action_r.prompt_tokens,
                    iter_ct + action_r.completion_tokens,
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
        if not step.prompt_ref and not step.get_domain_field("assess_handler"):
            errors.append(f"Step '{step.id}' missing prompt_ref")
        if not step.handler_inputs:
            errors.append(f"Step '{step.id}' missing handler_inputs")
        errors.extend(AssessLoopConfig.from_step(step).validate(step.id))
        return errors
