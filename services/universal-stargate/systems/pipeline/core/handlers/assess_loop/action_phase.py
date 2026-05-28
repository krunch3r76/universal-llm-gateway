"""
Action dispatch phase for the assess_loop_v1 handler.

Runs the (possibly multi-step) action chosen by an assess decision. Each
sub-step renders its prompt with the running ``action_ctx``, resolves its
model, calls it, strips configured XML tags, and feeds its output forward as
the next sub-step's artifact. Returns an :class:`ActionOutcome` carrying the
updated artifact plus token/latency accounting; exit-action and
iteration-completed event emission stay with the loop runner.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from .artifact_text import _strip_xml_tags

if TYPE_CHECKING:
    from ...schemas import StepConfig
    from ..assess_loop_config import AssessLoopConfig, LoopState
    from ..builtin import BaseHandler
    from ..protocol import PipelineContext


@dataclass(slots=True)
class ActionOutcome:
    """Result of dispatching one action's step sequence.

    ``artifact`` / ``artifact_raw`` are the post-action values (stripped and
    unstripped); ``action_model`` is the last resolved model id (used for the
    iteration-completed event); ``total_pt`` / ``total_ct`` are the summed
    prompt/completion tokens across the action's sub-steps; ``action_ms`` is the
    wall-clock latency of the whole action dispatch.
    """

    artifact: str
    artifact_raw: str
    action_model: str
    action_ms: float
    total_pt: int
    total_ct: int


async def run_action(
    handler: BaseHandler,
    step: StepConfig,
    context: PipelineContext,
    cfg: AssessLoopConfig,
    base_ctx: dict[str, Any],
    artifact: str,
    decision: dict[str, Any],
    action: str,
    reason: str,
    iteration: int,
    cached_sys: str | None,
    state: LoopState,
) -> ActionOutcome:
    """Dispatch ``action`` (one or more sub-steps) and return its outcome.

    Builds ``action_ctx`` from ``base_ctx`` plus the current artifact and the
    assess metadata (``assess_action`` / ``assess_target`` / ``assess_reason``),
    then runs each step from ``cfg.get_action_steps(action)`` sequentially:
    output of step N becomes the artifact fed into step N+1. Tokens accumulate
    into ``state`` and into the returned per-action totals. ``iteration`` is the
    current loop index, used only to build the per-call ``call_label``.
    """
    action_ctx = {
        **base_ctx,
        cfg.artifact_key: artifact,
        "assess_action": action,
        "assess_target": decision.get("target", ""),
        "assess_reason": reason,
    }
    action_steps = cfg.get_action_steps(action)
    t1 = time.monotonic()
    action_total_pt = 0
    action_total_ct = 0
    action_model = step.model_ref
    artifact_raw = artifact
    for sub_idx, action_step in enumerate(action_steps):
        sub_rendered = handler._render_prompt(
            action_step.prompt_ref, action_ctx, context
        )
        action_model = await handler._resolve_model_alias_async(
            action_step.model_ref or step.model_ref,
            context,
            step_name=step.name,
        )
        sub_r = await handler._call_model(
            action_model,
            sub_rendered.user_prompt,
            step,
            context,
            cached_sys or sub_rendered.system_prompt,
            temperature=cfg.temperature,
            json_schema=action_step.schema,
            call_label=f"action_{action}_{iteration}_{sub_idx}",
        )
        state.add_tokens(sub_r.prompt_tokens, sub_r.completion_tokens)
        action_total_pt += sub_r.prompt_tokens
        action_total_ct += sub_r.completion_tokens
        artifact_raw = sub_r.content.strip()
        artifact = _strip_xml_tags(artifact_raw, cfg.strip_xml_tags)
        action_ctx = {**action_ctx, cfg.artifact_key: artifact}
    action_ms = (time.monotonic() - t1) * 1000

    return ActionOutcome(
        artifact=artifact,
        artifact_raw=artifact_raw,
        action_model=action_model,
        action_ms=action_ms,
        total_pt=action_total_pt,
        total_ct=action_total_ct,
    )
