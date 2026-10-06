"""Read house card from cortex files."""

from __future__ import annotations

from typing import Any, override

from maestro_induct.parse import parse_card
from systems.pipeline.core.handlers.builtin import BaseHandler
from systems.pipeline.core.handlers.protocol import StepOutput

from . import _clients


class MaestroInductFetchHouseHandler(BaseHandler):
    step_type = "maestro_induct_fetch_house_v1"

    @override
    async def execute(self, step: Any, context: Any) -> StepOutput:
        resolve = _clients.step_output_json(context.outputs, "resolve")
        if resolve.get("invalid_root"):
            return StepOutput(raw="", json={"skipped": True})
        root = str(resolve.get("root"))
        uri = f"cortex://notes/system/threads/{root}-card.md"
        res = _clients.read_cortex_file(uri)
        if isinstance(res, dict):
            return StepOutput(raw="", json={"error": res["error"], "errors": [res["error"]]})
        text, sha = res
        parsed = parse_card(text)
        payload = {**parsed, "card_sha256": sha, "card_uri": uri}
        return StepOutput(raw="", json=payload)
