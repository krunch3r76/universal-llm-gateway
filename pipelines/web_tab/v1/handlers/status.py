"""web-tab-status pipeline step."""

from __future__ import annotations

import json
from typing import Any, override

from systems.pipeline.core.handlers.builtin import BaseHandler
from systems.pipeline.core.handlers.protocol import StepOutput
from web_tab.observation import ConnectorPlaneInput
from web_tab.status_report import build_status_report


class WebTabStatusHandler(BaseHandler):
    step_type = "web_tab_status_v1"

    @override
    async def execute(self, step: Any, context: Any) -> StepOutput:
        opts = getattr(context, "options", {}) or {}
        house_id = str(opts.get("house_id") or opts.get("house") or "")
        agent = str(opts.get("agent") or opts.get("from_agent") or "orion")
        if not house_id:
            bad = {"ok": False, "error": "missing house_id in pipeline_options"}
            return StepOutput(raw=json.dumps(bad), json=bad)

        conn = ConnectorPlaneInput(
            connector_alive=opts.get("connector_alive"),
            last_attested_turn=opts.get("last_attested_turn"),
            attestation_source=opts.get("attestation_source"),
        )
        report = await build_status_report(
            house_id=house_id,
            agent=agent,
            cdp_url=str(opts.get("cdp_url") or "http://127.0.0.1:9342"),
            target_id=(str(opts["target_id"]) if opts.get("target_id") else None),
            surface=str(opts.get("surface") or "chatgpt"),
            connector=conn,
            binding_resolve=opts.get("binding_resolve"),
        )
        return StepOutput(raw=json.dumps(report), json=report)
