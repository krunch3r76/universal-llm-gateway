"""Fetch latest terminal conductor row from GIW dispatch ledger."""

from __future__ import annotations

import json
from typing import Any, override

from operator_hop_harvest.ledger import (
    fetch_latest_terminal_conductor,
    record_json_dict,
)
from systems.pipeline.core.handlers.builtin import BaseHandler
from systems.pipeline.core.handlers.protocol import StepOutput

from ._clients import step_output_json


class OperatorHopHarvestFetchLedgerHandler(BaseHandler):
    step_type = "operator_hop_harvest_fetch_ledger_v1"

    @override
    async def execute(self, step: Any, context: Any) -> StepOutput:
        outputs = getattr(context, "step_outputs", {}) or {}
        resolved = step_output_json(outputs, "resolve")
        worker = str(resolved.get("worker_thread_id") or "")
        fetched = fetch_latest_terminal_conductor(worker) if worker else {
            "row": None,
            "ledger_unreachable": True,
        }
        row = fetched.get("row")
        ledger_unreachable = bool(fetched.get("ledger_unreachable"))
        if not isinstance(row, dict):
            out = {
                "conductor_row": None,
                "dispatch_id": None,
                "record_json": {},
                "ledger_unreachable": ledger_unreachable,
                "consult_pending_continue_owed": False,
            }
            return StepOutput(raw=json.dumps(out), json=out)
        rec = record_json_dict(row)
        out = {
            "conductor_row": {
                "dispatch_id": row.get("dispatch_id"),
                "status": row.get("status"),
                "thread_id": row.get("thread_id"),
                "hop_seq": row.get("hop_seq"),
            },
            "dispatch_id": str(row.get("dispatch_id") or ""),
            "record_json": rec,
            "scoreboard_uri": str(row.get("scoreboard_uri") or ""),
            "closeout_turn": row.get("closeout_turn"),
            "closeout_body": str(row.get("closeout_body") or ""),
            "ledger_unreachable": ledger_unreachable,
            "consult_pending_continue_owed": bool(
                row.get("consult_pending_continue_owed")
            ),
        }
        return StepOutput(raw=json.dumps(out), json=out)
