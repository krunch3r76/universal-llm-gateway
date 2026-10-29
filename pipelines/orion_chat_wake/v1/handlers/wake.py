"""One pipeline step: paste an Orion pointer wake via CDP."""

from __future__ import annotations

import json
from typing import Any, override

from systems.pipeline.core.handlers.builtin import BaseHandler
from systems.pipeline.core.handlers.protocol import StepOutput
from web_chat_relay.orion_wake import (
    DEFAULT_ORION_CDP_URL,
    deliver_orion_chat_wake,
    parse_turn_numbers,
)


class OrionChatWakeHandler(BaseHandler):
    step_type = "orion_chat_wake_v1"

    @override
    async def execute(self, step: Any, context: Any) -> StepOutput:
        opts = getattr(context, "options", {}) or {}
        turn_raw = opts.get("turn")
        has_turn_ids = bool(opts.get("turn_ids") or opts.get("covered_turn_ids"))
        if not has_turn_ids and parse_turn_numbers(turn_raw) is None:
            if turn_raw not in (None, "", 0, "0"):
                bad = {
                    "ok": False,
                    "reason": "bad_turn",
                    "pasted": False,
                    "delivered": False,
                }
                return StepOutput(raw=json.dumps(bad), json=bad)
        turn_arg: int | str = turn_raw if turn_raw is not None else 0
        result = await deliver_orion_chat_wake(
            thread=str(opts.get("thread") or ""),
            turn=turn_arg,
            from_agent=str(opts.get("from_agent") or ""),
            conversation_url=str(opts.get("conversation_url") or "") or None,
            cdp_url=str(opts.get("cdp_url") or "") or DEFAULT_ORION_CDP_URL,
            pipeline_options=opts,
        )
        return StepOutput(raw=json.dumps(result), json=result)
