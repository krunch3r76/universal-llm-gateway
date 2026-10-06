"""Call libs.maestro_induct.assemble_packet."""

from __future__ import annotations

from typing import Any, override

from maestro_induct.assemble import assemble_packet
from systems.pipeline.core.handlers.builtin import BaseHandler
from systems.pipeline.core.handlers.protocol import StepOutput

from . import _clients


class MaestroInductAssembleHandler(BaseHandler):
    step_type = "maestro_induct_assemble_v1"

    @override
    async def execute(self, step: Any, context: Any) -> StepOutput:
        resolve = _clients.step_output_json(context.outputs, "resolve")
        options = dict(resolve.get("options") or {})
        options["root"] = resolve.get("root")
        sections = {
            "resolve": resolve,
            "fetch_house": _clients.step_output_json(context.outputs, "fetch_house"),
            "fetch_checkpoint": _clients.step_output_json(context.outputs, "fetch_checkpoint"),
            "fetch_continuity": _clients.step_output_json(context.outputs, "fetch_continuity"),
            "fetch_journal": _clients.step_output_json(context.outputs, "fetch_journal"),
            "fetch_runbook": _clients.step_output_json(context.outputs, "fetch_runbook"),
            "fetch_scores": _clients.step_output_json(context.outputs, "fetch_scores"),
            "enumerate_lanes": _clients.step_output_json(context.outputs, "enumerate_lanes"),
            "fetch_lanes": _clients.step_output_json(context.outputs, "fetch_lanes"),
            "fetch_consults": _clients.step_output_json(context.outputs, "fetch_consults"),
        }
        packet = assemble_packet(sections=sections, options=options)
        return StepOutput(raw="", json=packet)
