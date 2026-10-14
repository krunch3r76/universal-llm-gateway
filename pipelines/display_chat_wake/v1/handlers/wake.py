"""One pipeline step that pastes a display wake or returns ok false.

The run completes either way. A title mismatch is a finished result, not
a failed step. claude.ai reads ``ok`` and does not retry a mismatch as
an infrastructure error.
"""

from __future__ import annotations

import json
from typing import Any, override

from cursor_bridge.wake_delivery import deliver_wake
from systems.pipeline.core.handlers.builtin import BaseHandler
from systems.pipeline.core.handlers.protocol import StepOutput


class DisplayChatWakeHandler(BaseHandler):
    """Paste ``BRIDGE_WAKE`` when the sidebar title matches the target.

    Options are ``display``, ``app``, ``thread``, ``slug``, ``title``, and
    ``turn``. ``title`` overrides ``"<thread> <slug>"`` when provided.
    The step awaits ``deliver_wake`` and returns that dict as JSON. It
    does not raise when the gate refuses, so the pipeline run completes
    with ``ok`` false. No model call.
    """

    step_type = "display_chat_wake_v1"

    @override
    async def execute(self, step: Any, context: Any) -> StepOutput:
        opts = getattr(context, "options", {}) or {}
        result = await deliver_wake(
            display=opts.get("display"),
            app=opts.get("app"),
            thread=opts.get("thread"),
            slug=opts.get("slug"),
            title=opts.get("title"),
            turn=opts.get("turn"),
        )
        raw = json.dumps(result)
        return StepOutput(raw=raw, json=result)
