"""web-tab-probe pipeline step."""

from __future__ import annotations

import json
from typing import Any, override

from systems.pipeline.core.handlers.builtin import BaseHandler
from systems.pipeline.core.handlers.protocol import StepOutput
from web_tab.conversation_id import conversation_id_from_url
from web_tab.probe import probe_target


def _binding_conversation_id(opts: dict[str, Any]) -> str | None:
    raw = opts.get("binding_conversation_id")
    if isinstance(raw, str) and raw.strip():
        return raw.strip()
    url = opts.get("binding_conversation_url")
    if isinstance(url, str):
        return conversation_id_from_url(url)
    return None


class WebTabProbeHandler(BaseHandler):
    step_type = "web_tab_probe_v1"

    @override
    async def execute(self, step: Any, context: Any) -> StepOutput:
        opts = getattr(context, "options", {}) or {}
        result = await probe_target(
            cdp_url=(str(opts["cdp_url"]) if opts.get("cdp_url") else None),
            target_id=(str(opts["target_id"]) if opts.get("target_id") else None),
            surface=str(opts.get("surface") or "chatgpt"),
            house_id=(str(opts["house_id"]) if opts.get("house_id") else None),
            binding_conversation_id=_binding_conversation_id(opts),
        )
        return StepOutput(raw=json.dumps(result), json=result)
