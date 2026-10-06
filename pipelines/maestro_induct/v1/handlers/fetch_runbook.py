"""Read first runbook URI from card."""

from __future__ import annotations

from typing import Any, override

from maestro_induct.parse import parse_runbook_steps
from systems.pipeline.core.handlers.builtin import BaseHandler
from systems.pipeline.core.handlers.protocol import StepOutput

from . import _clients


class MaestroInductFetchRunbookHandler(BaseHandler):
    step_type = "maestro_induct_fetch_runbook_v1"

    @override
    async def execute(self, step: Any, context: Any) -> StepOutput:
        resolve = _clients.step_output_json(context.outputs, "resolve")
        if resolve.get("invalid_root"):
            return StepOutput(raw="", json={"skipped": True})
        if "runbook" in (resolve.get("skipped") or []):
            return StepOutput(raw="", json={"skipped": True})
        house = _clients.step_output_json(context.outputs, "fetch_house")
        house_err = house.get("error") if isinstance(house.get("error"), dict) else None
        if house_err and house_err.get("kind") == "files_root_unset":
            return StepOutput(raw="", json={"error": house_err, "errors": [house_err]})
        uris = house.get("runbook_uris") or []
        if not uris:
            err = {"kind": "runbook_missing", "message": "no runbook URI on card"}
            return StepOutput(raw="", json={"error": err, "errors": [err]})
        uri = uris[0]
        res = _clients.read_cortex_file(uri)
        if isinstance(res, dict):
            return StepOutput(raw="", json=res)
        text, _sha = res
        steps = parse_runbook_steps(text)
        return StepOutput(raw="", json={"uri": uri, "steps": steps})
