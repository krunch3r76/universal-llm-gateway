"""Build lane records from tail turns."""

from __future__ import annotations

from typing import Any, override

from maestro_induct.lanes import build_lane_record
from systems.pipeline.core.handlers.builtin import BaseHandler
from systems.pipeline.core.handlers.protocol import StepOutput

from . import _clients


class MaestroInductFetchLanesHandler(BaseHandler):
    step_type = "maestro_induct_fetch_lanes_v1"

    @override
    async def execute(self, step: Any, context: Any) -> StepOutput:
        resolve = _clients.step_output_json(context.outputs, "resolve")
        if resolve.get("invalid_root") or "lanes" in (resolve.get("skipped") or []):
            return StepOutput(raw="", json={"skipped": True})
        root = str(resolve.get("root"))
        deadline = resolve.get("deadline_epoch")
        enum = _clients.step_output_json(context.outputs, "enumerate_lanes")
        children = enum.get("children") or []
        lanes: list[dict[str, Any]] = []
        errors: list[dict[str, Any]] = []
        for lane in children:
            tid = str(lane.get("thread_id"))
            payload, status = await _clients.bus_get(
                "/turns",
                params={"thread": tid, "last": 4},
                deadline_epoch=deadline,
            )
            if status == 504:
                err = {"kind": "deadline_exceeded", "section": "lanes", "threads": [tid]}
                errors.append(err)
                break
            turns = payload.get("turns") or []
            lanes.append(build_lane_record(lane=lane, tail_turns=turns, root=root))
        return StepOutput(raw="", json={"lanes": lanes, "errors": errors})
