"""Discover scoreboard slugs via cortex relationships or regex fallback."""

from __future__ import annotations

from typing import Any, override

from maestro_induct.parse import discover_scores_regex, parse_relationship_todos
from systems.pipeline.core.handlers.builtin import BaseHandler
from systems.pipeline.core.handlers.protocol import StepOutput

from . import _clients


class MaestroInductDiscoverScoresHandler(BaseHandler):
    step_type = "maestro_induct_discover_scores_v1"

    @override
    async def execute(self, step: Any, context: Any) -> StepOutput:
        resolve = _clients.step_output_json(context.outputs, "resolve")
        if resolve.get("invalid_root"):
            return StepOutput(raw="", json={"skipped": True})
        root = str(resolve.get("root"))
        deadline = resolve.get("deadline_epoch")
        entity = f"document:{root}-continuity"
        resp = await _clients.cortex_dispatch(
            "relationships", {"entity_id": entity}, deadline_epoch=deadline
        )
        slugs: list[str] = []
        discovered_by = "regex"
        if "error" not in resp:
            slugs = parse_relationship_todos(resp, root=root)
            if slugs:
                discovered_by = "graph"
        if not slugs:
            house = _clients.step_output_json(context.outputs, "fetch_house")
            cp = _clients.step_output_json(context.outputs, "fetch_checkpoint")
            objective = ""
            for h in house.get("headings") or []:
                if h.get("title") == "Objective":
                    objective = h.get("body") or ""
            anchor = str(cp.get("anchor") or "")
            slugs = discover_scores_regex(objective=objective, checkpoint_anchor=anchor)
        return StepOutput(raw="", json={"slugs": slugs, "discovered_by": discovered_by})
