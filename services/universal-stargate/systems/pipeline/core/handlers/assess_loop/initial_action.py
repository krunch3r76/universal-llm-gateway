"""
Optional pre-loop initial action for the assess_loop_v1 handler.

When the artifact is empty and ``cfg.initial_action`` is configured, one model
call generates the artifact from scratch before the assess loop begins (the
"step-owned" bootstrap mode). Emits :class:`AssessLoopInitialActionCompleted`
and accumulates tokens into the shared :class:`LoopState`.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

from .artifact_text import _strip_xml_tags

if TYPE_CHECKING:
    from typing import Any

    from ...schemas import StepConfig
    from ..assess_loop_config import AssessLoopConfig, LoopState
    from ..builtin import BaseHandler
    from ..protocol import PipelineContext


async def run_initial_action(
    handler: BaseHandler,
    step: StepConfig,
    context: PipelineContext,
    cfg: AssessLoopConfig,
    base_ctx: dict[str, Any],
    artifact: str,
    cached_sys: str | None,
    state: LoopState,
) -> tuple[str, str]:
    """Run the configured initial action and return ``(artifact, artifact_raw)``.

    Renders the initial action's prompt with the current (empty) artifact,
    resolves the action model, calls it with ``call_label="initial"``, strips
    configured XML bookkeeping tags from the result, accumulates tokens, and
    emits :class:`AssessLoopInitialActionCompleted` when a recorder is present.

    The caller is responsible for the ``cfg.initial_action and not artifact``
    guard; this function unconditionally runs the action.
    """
    from ...events.assess_loop import AssessLoopInitialActionCompleted

    initial_ctx = {**base_ctx, cfg.artifact_key: artifact}
    initial_rendered = handler._render_prompt(
        cfg.get_action_prompt_ref(cfg.initial_action), initial_ctx, context
    )
    initial_model = await handler._resolve_model_alias_async(
        cfg.get_action_model_ref(cfg.initial_action, step.model_ref),
        context,
        step_name=step.name,
    )
    t_pre = time.monotonic()
    pre_r = await handler._call_model(
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
    recorder = context.recorder
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
    return artifact, artifact_raw
