"""web-tab-status pipeline step."""

from __future__ import annotations

import json
from typing import Any, override

from systems.pipeline.core.handlers.builtin import BaseHandler
from systems.pipeline.core.handlers.protocol import StepOutput
from web_tab.status_report import build_status_report


class WebTabStatusHandler(BaseHandler):
    """Build the read-only web-tab status report for one house and agent.

    Caller identity in pipeline options is ignored. Connector evidence comes
    from the server-stored attestation read, not from the request body.
    """

    step_type = "web_tab_status_v1"

    @override
    async def execute(self, step: Any, context: Any) -> StepOutput:
        opts = getattr(context, "options", {}) or {}
        house_id = str(opts.get("house_id") or opts.get("house") or "")
        agent = str(opts.get("agent") or opts.get("from_agent") or "")
        if not house_id:
            bad = {"ok": False, "error": "missing house_id in pipeline_options"}
            return StepOutput(raw=json.dumps(bad), json=bad)
        if not agent:
            bad = {"ok": False, "error": "missing agent in pipeline_options"}
            return StepOutput(raw=json.dumps(bad), json=bad)

        report = await build_status_report(
            house_id=house_id,
            agent=agent,
            cdp_url=(str(opts["cdp_url"]) if opts.get("cdp_url") else None),
            target_id=(str(opts["target_id"]) if opts.get("target_id") else None),
            surface=str(opts.get("surface") or "chatgpt"),
        )
        return StepOutput(raw=json.dumps(report), json=report)
