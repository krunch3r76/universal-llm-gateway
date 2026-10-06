"""Fetch scoreboard tip bodies per slug."""

from __future__ import annotations

from typing import Any, override

from maestro_induct.parse import parse_scoreboard_tip
from systems.pipeline.core.handlers.builtin import BaseHandler
from systems.pipeline.core.handlers.protocol import StepOutput

from . import _clients


class MaestroInductFetchScoresHandler(BaseHandler):
    step_type = "maestro_induct_fetch_scores_v1"

    @override
    async def execute(self, step: Any, context: Any) -> StepOutput:
        discover = _clients.step_output_json(context.outputs, "discover_scores")
        if discover.get("skipped"):
            return StepOutput(raw="", json={"skipped": True})
        slugs = discover.get("slugs") or []
        discovered_by = discover.get("discovered_by") or "graph"
        scores: list[dict[str, Any]] = []
        errors: list[dict[str, Any]] = []
        for slug in slugs:
            uri = f"cortex://notes/system/scoreboards/{slug}-scoreboard.md"
            res = _clients.read_cortex_file(uri)
            if isinstance(res, dict):
                kind = res["error"]["kind"]
                if kind == "file_missing":
                    item = {"slug": slug, "error": {"kind": "scoreboard_missing", "uri": uri}}
                else:
                    item = {"slug": slug, "error": res["error"]}
                scores.append(item)
                errors.append({**item["error"], "section": "scores", "item": slug})
                continue
            text, sha = res
            item = parse_scoreboard_tip(text, slug=slug, discovered_by=discovered_by)
            item["sha256"] = sha
            scores.append(item)
        return StepOutput(raw="", json={"scores": scores, "errors": errors})
