"""Thin wrapper — libs/operator_hop_harvest parse functions."""

from __future__ import annotations

import json
from typing import Any, override

from claude_bundles.conductor_stop import last_next_admit_payload
from operator_hop_harvest.parse import (
    compute_next_admit_divergent,
    parse_conductor_closeout,
    parse_harvest_recipe,
    parse_scoreboard,
)
from systems.pipeline.core.handlers.builtin import BaseHandler
from systems.pipeline.core.handlers.protocol import StepOutput

from ._clients import step_output_json


class OperatorHopHarvestParseHandler(BaseHandler):
    step_type = "operator_hop_harvest_parse_v1"

    @override
    async def execute(self, step: Any, context: Any) -> StepOutput:
        outputs = getattr(context, "step_outputs", {}) or {}
        bus = step_output_json(outputs, "fetch_bus")
        score = step_output_json(outputs, "fetch_scoreboard")
        ledger = step_output_json(outputs, "fetch_ledger")
        wait_step = step_output_json(outputs, "fetch_wait")
        resolved = step_output_json(outputs, "resolve")

        closeout_body = str(bus.get("closeout_turn_body") or "")
        row = ledger.get("conductor_row") or {}
        rec = ledger.get("record_json") or {}
        closeout_turn = bus.get("closeout_turn")
        if not isinstance(closeout_turn, int):
            closeout_turn = rec.get("closeout_turn") if isinstance(rec.get("closeout_turn"), int) else 0

        conductor = parse_conductor_closeout(
            body=closeout_body,
            dispatch_id=str(ledger.get("dispatch_id") or row.get("dispatch_id") or ""),
            hop_seq=row.get("hop_seq") if isinstance(row.get("hop_seq"), int) else None,
            work_outcome=None,
            degraded_reason=None,
            closeout_turn=int(closeout_turn),
            closeout_uri=None,
            usage=None,
            branch=None,
            head_sha=None,
            commits_ahead=None,
        )
        scoreboard = parse_scoreboard(
            body=str(score.get("scoreboard_body") or ""),
            uri=str(score.get("scoreboard_uri") or ""),
            sha256=str(score.get("scoreboard_sha256") or ""),
        )
        harvest_next = last_next_admit_payload(str(bus.get("harvest_turn_body") or ""))
        divergent = compute_next_admit_divergent(
            closeout_next_admit=conductor.get("next_admit") if isinstance(conductor.get("next_admit"), str) else None,
            scoreboard_next_admit=scoreboard.get("next_admit_tip") if isinstance(scoreboard.get("next_admit_tip"), str) else None,
            harvest_next_admit=harvest_next,
        )
        recipe = parse_harvest_recipe(
            worker_thread_id=str(resolved.get("worker_thread_id") or ""),
            summoning_thread_id=str(resolved.get("summoning_thread_id") or ""),
            closeout_turn=int(closeout_turn),
            summoning_admit_turn=int(bus.get("summoning_admit_turn") or 0),
        )
        parsed = {
            "conductor": conductor,
            "scoreboard": scoreboard,
            "wait": wait_step.get("wait") or {},
            "harvest_recipe": recipe,
            "next_admit_divergent": divergent,
            "harvest_next_admit": harvest_next,
        }
        return StepOutput(raw=json.dumps(parsed), json={"parsed": parsed})
