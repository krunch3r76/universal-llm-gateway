"""
Single-iteration assess phase for the assess_loop_v1 handler.

Runs one assessment — either a programmatic Python handler (no LLM) or an LLM
call whose JSON response is parsed and sanitized. Returns an :class:`AssessOutcome`
describing either a usable ``decision`` or an early-exit ``exit_reason``
(``assess_handler_error`` / ``json_parse_failure``). Token accounting and the
error-path :func:`emit_iteration_completed` emission happen here; the loop runner
owns the ``break`` and all decision-branch state mutation.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from universal_logging import get_logger

from ..assess_loop_config import (
    PROGRAMMATIC_ASSESS_HANDLERS,
    emit_iteration_completed,
    sanitize_repeated_items_decision,
)

if TYPE_CHECKING:
    from ...schemas import StepConfig
    from ..assess_loop_config import AssessLoopConfig, LoopState
    from ..builtin import BaseHandler
    from ..protocol import PipelineContext

logger = get_logger(__name__)


@dataclass(slots=True)
class AssessOutcome:
    """Result of one assess iteration.

    Exactly one of ``decision`` / ``exit_reason`` is meaningful:
    - ``decision`` set, ``exit_reason`` None → proceed to decision branching.
    - ``exit_reason`` set ("assess_handler_error" | "json_parse_failure"),
      ``decision`` None → the runner records the reason and breaks. The
      terminating ``emit_iteration_completed`` has already fired here.

    ``assess_ms`` / ``iter_pt`` / ``iter_ct`` carry per-iteration latency and
    token counts forward for the action-phase event accounting.
    """

    decision: dict[str, Any] | None
    exit_reason: str | None
    assess_ms: float
    iter_pt: int
    iter_ct: int


async def run_assess(
    handler: BaseHandler,
    step: StepConfig,
    context: PipelineContext,
    cfg: AssessLoopConfig,
    assess_ctx: dict[str, Any],
    iteration: int,
    resolved: dict[str, Any],
    artifact_raw: str,
    cached_sys: str | None,
    state: LoopState,
) -> AssessOutcome:
    """Perform one assessment and return its :class:`AssessOutcome`.

    Programmatic path (``cfg.assess_handler`` set): call the registered Python
    function with the unstripped ``artifact_raw``; on exception, emit the
    terminal iteration event and return an ``assess_handler_error`` outcome.

    LLM path: resolve the assess model (pool round-robin), render the assess
    prompt, call the model with the assess schema, accumulate tokens, then parse
    and sanitize the JSON decision; on ``JSONDecodeError`` emit the terminal
    iteration event and return a ``json_parse_failure`` outcome.
    """
    recorder = context.recorder

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
            return AssessOutcome(
                decision=None,
                exit_reason="assess_handler_error",
                assess_ms=(time.monotonic() - t0) * 1000,
                iter_pt=0,
                iter_ct=0,
            )
        assess_ms = (time.monotonic() - t0) * 1000
        iter_pt = 0
        iter_ct = 0
    else:
        assess_model = await handler._resolve_model_alias_async(
            cfg.get_assess_model_ref(iteration, step.model_ref),
            context,
            step_name=step.name,
        )
        rendered = handler._render_prompt(step.prompt_ref, assess_ctx, context)
        sys_p = cached_sys or rendered.system_prompt
        t0 = time.monotonic()

        assess_r = await handler._call_model(
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
            return AssessOutcome(
                decision=None,
                exit_reason="json_parse_failure",
                assess_ms=assess_ms,
                iter_pt=iter_pt,
                iter_ct=iter_ct,
            )

    return AssessOutcome(
        decision=decision,
        exit_reason=None,
        assess_ms=assess_ms,
        iter_pt=iter_pt,
        iter_ct=iter_ct,
    )
