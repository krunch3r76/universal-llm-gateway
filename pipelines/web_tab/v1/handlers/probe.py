"""web-tab-probe pipeline step."""

from __future__ import annotations

import json
from typing import Any, override

from systems.pipeline.core.handlers.builtin import BaseHandler
from systems.pipeline.core.handlers.protocol import StepOutput
from web_tab.probe import probe_target


class WebTabProbeHandler(BaseHandler):
    step_type = "web_tab_probe_v1"

    @override
    async def execute(self, step: Any, context: Any) -> StepOutput:
        opts = getattr(context, "options", {}) or {}
        result = await probe_target(
            cdp_url=str(opts.get("cdp_url") or "http://127.0.0.1:9342"),
            target_id=(str(opts["target_id"]) if opts.get("target_id") else None),
            surface=str(opts.get("surface") or "chatgpt"),
            house_id=(str(opts["house_id"]) if opts.get("house_id") else None),
        )
        return StepOutput(raw=json.dumps(result), json=result)
