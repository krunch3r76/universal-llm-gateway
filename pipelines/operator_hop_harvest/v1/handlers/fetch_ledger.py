"""Fetch latest terminal conductor row from GIW dispatch ledger."""

from __future__ import annotations

import json
from typing import Any, override

from operator_hop_harvest.ledger import (
    latest_terminal_conductor_for_thread,
    record_json_dict,
)
from systems.pipeline.core.handlers.builtin import BaseHandler
from systems.pipeline.core.handlers.protocol import StepOutput

from services.git_integration_worker.cursor_sdk_ledger_hop import (
    hop_fields_from_record_json,
)

from ._clients import step_output_json


class OperatorHopHarvestFetchLedgerHandler(BaseHandler):
    step_type = "operator_hop_harvest_fetch_ledger_v1"

    @override
    async def execute(self, step: Any, context: Any) -> StepOutput:
        outputs = getattr(context, "step_outputs", {}) or {}
        resolved = step_output_json(outputs, "resolve")
        worker = str(resolved.get("worker_thread_id") or "")
        row = latest_terminal_conductor_for_thread(worker) if worker else None
        if row is None:
            out = {"conductor_row": None, "dispatch_id": None, "record_json": {}}
            return StepOutput(raw=json.dumps(out), json=out)
        rec = record_json_dict(row)
        hop = hop_fields_from_record_json(str(row.get("record_json") or ""))
        out = {
            "conductor_row": {
                "dispatch_id": row.get("dispatch_id"),
                "status": row.get("status"),
                "thread_id": row.get("thread_id"),
                "hop_seq": hop.get("hop_seq"),
            },
            "dispatch_id": str(row.get("dispatch_id") or ""),
            "record_json": rec,
            "scoreboard_uri": str(rec.get("scoreboard_uri") or rec.get("scoreboard") or ""),
            "closeout_turn": rec.get("closeout_turn"),
            "closeout_body": rec.get("closeout_body") or row.get("closeout_body") or "",
        }
        return StepOutput(raw=json.dumps(out), json=out)
