"""List child lanes from lineage."""

from __future__ import annotations

from typing import Any, override

from maestro_induct.lanes import order_lanes_newest_first
from systems.pipeline.core.handlers.builtin import BaseHandler
from systems.pipeline.core.handlers.protocol import StepOutput

from . import _clients


class MaestroInductEnumerateLanesHandler(BaseHandler):
    step_type = "maestro_induct_enumerate_lanes_v1"

    @override
    async def execute(self, step: Any, context: Any) -> StepOutput:
        resolve = _clients.step_output_json(context.outputs, "resolve")
        if resolve.get("invalid_root"):
            return StepOutput(raw="", json={"skipped": True})
        root = str(resolve.get("root"))
        deadline = resolve.get("deadline_epoch")
        payload, status = await _clients.bus_get(
            f"/threads/{root}/lineage", deadline_epoch=deadline
        )
        if status == 404:
            err = {"kind": "lineage_not_found", "message": root}
            return StepOutput(
                raw="",
                json={
                    "error": err,
                    "errors": [err],
                    "lane_ids": [root],
                    "children": [],
                },
            )
        if status >= 400:
            err = {"kind": "lineage_error", "message": str(payload)}
            return StepOutput(
                raw="",
                json={"error": err, "errors": [err], "lane_ids": [root], "children": []},
            )
        children = order_lanes_newest_first(payload.get("children") or [])
        lane_ids = [root] + [str(c.get("thread_id")) for c in children]
        return StepOutput(
            raw="",
            json={"children": children, "lane_ids": lane_ids},
        )
