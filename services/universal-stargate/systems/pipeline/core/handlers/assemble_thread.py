"""``assemble_thread_v1`` — resolve the chat anchor and build the message prefix.

First step of the cortex-chat-openai compaction loop. Reads
``context.chat_id`` (set by Phase 1 substrate), resolves or creates the
``thread:openai-chat:{chat_id}`` anchor entity, and projects the last
``window_size`` non-superseded turn assertions into a
``[{role, content}]`` prefix consumed downstream by
``frontier_dispatch_request``'s ``handler_inputs.messages`` binding.

Output shape (JSON namespace on the step's StepOutput):

    {
        "messages":    [{"role": "user"|"assistant", "content": str}, ...],
        "anchor_id":   "thread:openai-chat:<chat_id>",
        "turn_index":  int  # next free turn slot (0 for a fresh thread)
    }

``messages`` is the *prefix only* — the current user turn is appended
by the caller (``resolve_messages``). The latency budget for this
handler is 50 ms per the cortex-chat-openai MVP plan; breaches warn,
never fail (Phase A measure-only stance per agent-bus thread 1091).
"""

from __future__ import annotations

import json
import time
from typing import TYPE_CHECKING

from universal_logging import get_logger

from ..events.compaction import PipelineCompactionAssembled
from .protocol import PipelineContext, StepOutput
from .registry import register_handler
from .thread_persistence import (
    build_referential_window,
    publish_compaction_event,
    require_thread_binding,
    resolve_or_create_anchor,
)

if TYPE_CHECKING:
    from ..schemas import StepConfig

logger = get_logger(__name__)

_DEFAULT_WINDOW_SIZE = 8
_LATENCY_BUDGET_MS = 50.0


@register_handler
class AssembleThreadV1Handler:
    """Resolve thread anchor + build text-only referential window prefix."""

    step_type = "assemble_thread_v1"

    async def execute(self, step: StepConfig, context: PipelineContext) -> StepOutput:
        start = time.monotonic()

        try:
            binding = require_thread_binding(context)
        except ValueError as exc:
            raise ValueError(
                f"Step '{step.id}': assemble_thread_v1 requires "
                f"context.chat_id or context.dispatch_thread_id"
            ) from exc

        # window_size is a static numeric domain field, matching the
        # rag_search_v1 precedent for top_k. Pipeline YAML may set it
        # via `window_size: 16` at step level; default 8 mirrors the
        # MVP spec's default_window.
        window_size_raw = step.get_domain_field("window_size", _DEFAULT_WINDOW_SIZE)
        try:
            window_size = int(window_size_raw)
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f"Step '{step.id}': window_size must be an integer, "
                f"got {window_size_raw!r}"
            ) from exc
        if window_size < 1:
            raise ValueError(
                f"Step '{step.id}': window_size must be >= 1, got {window_size}"
            )

        anchor_id, turn_index = await resolve_or_create_anchor(
            binding.kind, binding.key
        )
        messages = await build_referential_window(anchor_id, k=window_size)

        # Count distinct turn indices on anchor for assembled-event telemetry.
        total_turn_pairs = turn_index

        publish_compaction_event(
            context,
            PipelineCompactionAssembled,
            execution_id=context.execution_id,
            chat_id=binding.key,
            anchor_id=anchor_id,
            turn_index=turn_index,
            window_size=window_size,
            messages_count=len(messages),
            total_turn_pairs=total_turn_pairs,
        )

        duration_ms = (time.monotonic() - start) * 1000.0
        if duration_ms > _LATENCY_BUDGET_MS:
            # Phase A measure-only: warn but never fail. Hard gate
            # promotes to Phase B per agent-bus thread 1091 turn 5104.
            logger.warning(
                "assemble_thread_v1 latency budget blown: %.2fms "
                "(chat_id=%s, turn_index=%d, window_size=%d)",
                duration_ms,
                binding.key,
                turn_index,
                window_size,
            )

        payload = {
            "messages": messages,
            "anchor_id": anchor_id,
            "turn_index": turn_index,
        }
        return StepOutput(raw=json.dumps(payload), json=payload)
