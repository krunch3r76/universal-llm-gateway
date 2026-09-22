"""Resolve worker and summoning thread ids from pipeline options."""

from __future__ import annotations

import json
from typing import Any, override

from systems.pipeline.core.handlers.builtin import BaseHandler
from systems.pipeline.core.handlers.protocol import StepOutput

from ._clients import bus_get


class OperatorHopHarvestResolveHandler(BaseHandler):
    step_type = "operator_hop_harvest_resolve_v1"

    @override
    async def execute(self, step: Any, context: Any) -> StepOutput:
        options: dict[str, Any] = getattr(context, "options", {}) or {}
        worker = str(options.get("thread") or "").strip()
        if not worker:
            err = {"error": "missing required pipeline_options.thread"}
            return StepOutput(raw=json.dumps(err), json=err, error=err["error"])
        summoning = str(options.get("summoning_thread") or "").strip()
        if not summoning:
            payload, status = await bus_get(f"/threads/{worker}")
            if status < 400 and isinstance(payload, dict):
                parent = payload.get("parent_thread") or payload.get("thread", {}).get(
                    "parent_thread"
                )
                if isinstance(parent, str) and parent.strip():
                    summoning = parent.strip()
        include_summary = options.get("include_summary") is True
        out = {
            "worker_thread_id": worker,
            "summoning_thread_id": summoning,
            "include_summary": include_summary,
        }
        return StepOutput(raw=json.dumps(out), json=out)
