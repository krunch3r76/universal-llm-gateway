"""One pipeline step that selects and pastes a display wake or returns ok false.

The run completes either way. A gate refusal (busy chat, non-empty draft,
or missing row) is a finished result, not a failed step. Callers read
``ok`` and ``delivered`` without retrying a refusal as an infrastructure
error.
"""

from __future__ import annotations

import json
from typing import Any, override

from cursor_bridge.wake_delivery import deliver_wake
from systems.pipeline.core.handlers.builtin import BaseHandler
from systems.pipeline.core.handlers.protocol import StepOutput


class DisplayChatWakeHandler(BaseHandler):
    """Select (when needed) and paste ``BRIDGE_WAKE`` into the target chat.

    Options are ``display``, ``app``, ``thread``, ``slug``, ``title``,
    ``agent_id``, ``select``, ``turn``, and optional ``text`` (exchange paste
    for thread 19627 cursor only). ``agent_id`` matches first when
    provided; ``title`` overrides ``"<thread> <slug>"``. The step awaits
    ``deliver_wake`` and returns that dict as JSON without raising on gate
    refusals. No model call.
    """

    step_type = "display_chat_wake_v1"

    @override
    async def execute(self, step: Any, context: Any) -> StepOutput:
        opts = getattr(context, "options", {}) or {}
        select_opt = opts.get("select")
        result = await deliver_wake(
            display=opts.get("display"),
            app=opts.get("app"),
            thread=opts.get("thread"),
            slug=opts.get("slug"),
            title=opts.get("title"),
            agent_id=opts.get("agent_id"),
            select=True if select_opt is None else bool(select_opt),
            turn=opts.get("turn"),
            text=str(opts.get("text") or ""),
        )
        raw = json.dumps(result)
        return StepOutput(raw=raw, json=result)
