"""Assemble capped R6 view."""

from __future__ import annotations

import json
from typing import Any, override

from operator_hop_harvest.assemble import (
    assemble_operator_hop_view,
    cap_view_json_bytes,
)
from systems.pipeline.core.handlers.builtin import BaseHandler
from systems.pipeline.core.handlers.protocol import StepOutput

from ._clients import step_output_json


class OperatorHopHarvestAssembleHandler(BaseHandler):
    step_type = "operator_hop_harvest_assemble_v1"

    @override
    async def execute(self, step: Any, context: Any) -> StepOutput:
        outputs = getattr(context, "step_outputs", {}) or {}
        parsed_wrap = step_output_json(outputs, "parse")
        parsed = parsed_wrap.get("parsed") if isinstance(parsed_wrap.get("parsed"), dict) else parsed_wrap
        resolved = step_output_json(outputs, "resolve")

        worker_id = str(resolved.get("worker_thread_id") or "")
        summoning_id = str(resolved.get("summoning_thread_id") or "")
        ledger_out = step_output_json(outputs, "fetch_ledger")
        auto = bool(ledger_out.get("consult_pending_continue_owed"))
        reason = (
            "consult_pending_continue_owed"
            if auto
            else "bare_consult_pending_no_auto_continue"
        )
        manual = (
            ""
            if auto
            else (
                "team_dispatch(reuse_thread=<worker>, contract=conductor, lane=B, "
                "source_ref=<work_key>)"
            )
        )

        view = assemble_operator_hop_view(
            worker_thread={"id": worker_id, "slug": f"agent-bus:{worker_id}"},
            summoning_thread={"id": summoning_id, "slug": f"agent-bus:{summoning_id}"},
            conductor=parsed.get("conductor") or {},
            scoreboard=parsed.get("scoreboard") or {},
            wait=parsed.get("wait") or {},
            job=None,
            harvest_recipe=parsed.get("harvest_recipe") or {},
            continuation={"auto": auto, "reason": reason, "manual_recipe": manual},
            next_admit_divergent=bool(parsed.get("next_admit_divergent")),
        )
        view = cap_view_json_bytes(view)
        return StepOutput(raw=json.dumps(view), json={"view": view})
