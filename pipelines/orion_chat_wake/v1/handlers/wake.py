"""One pipeline step: paste an Orion pointer wake via CDP."""

from __future__ import annotations

import json
from typing import Any, override

from systems.pipeline.core.handlers.builtin import BaseHandler
from systems.pipeline.core.handlers.protocol import StepOutput
from web_chat_relay.orion_hop import deliver_orion_chat_wake
from web_chat_relay.orion_wake import DEFAULT_ORION_CDP_URL


class OrionChatWakeHandler(BaseHandler):
    step_type = "orion_chat_wake_v1"

    @override
    async def execute(self, step: Any, context: Any) -> StepOutput:
        opts = getattr(context, "options", {}) or {}
        result = await deliver_orion_chat_wake(
            thread=str(opts.get("thread") or ""),
            turn=int(opts.get("turn") or 0),
            from_agent=str(opts.get("from_agent") or ""),
            conversation_url=str(opts.get("conversation_url") or "") or None,
            cdp_url=str(opts.get("cdp_url") or "") or DEFAULT_ORION_CDP_URL,
            pipeline_options=opts,
        )
        return StepOutput(raw=json.dumps(result), json=result)
