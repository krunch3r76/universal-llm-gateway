"""Read continuity hub markdown."""

from __future__ import annotations

from typing import Any, override

from maestro_induct.parse import parse_continuity_current
from systems.pipeline.core.handlers.builtin import BaseHandler
from systems.pipeline.core.handlers.protocol import StepOutput

from . import _clients


class MaestroInductFetchContinuityHandler(BaseHandler):
    step_type = "maestro_induct_fetch_continuity_v1"

    @override
    async def execute(self, step: Any, context: Any) -> StepOutput:
        resolve = _clients.step_output_json(context.outputs, "resolve")
        if resolve.get("invalid_root"):
            return StepOutput(raw="", json={"skipped": True})
        root = str(resolve.get("root"))
        from agent_bus_store.house_pools import load_continuity_card

        card = load_continuity_card(root)
        uri = (
            card.uri
            if card.status == "found" and card.uri
            else f"cortex://notes/system/threads/{root}-continuity.md"
        )
        res = _clients.read_cortex_file(uri)
        if isinstance(res, dict):
            return StepOutput(raw="", json=res)
        text, _sha = res
        return StepOutput(raw="", json=parse_continuity_current(text))
