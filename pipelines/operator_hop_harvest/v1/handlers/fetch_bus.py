"""Fetch agent-bus closeout and harvest turns."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, override

from systems.pipeline.core.handlers.builtin import BaseHandler
from systems.pipeline.core.handlers.protocol import StepOutput

from ._clients import bus_fetch_turn, bus_get, step_output_json


def _read_workspaces_uri(uri: str) -> str:
    if not uri.startswith("workspaces://"):
        return ""
    rest = uri[len("workspaces://") :]
    parts = rest.split("/", 1)
    if len(parts) != 2:
        return ""
    path = Path("/mnt/torus/projects/universal-llm-gateway") / parts[1]
    if path.is_file():
        return path.read_text(encoding="utf-8")
    alt = Path("/mnt/torus/projects") / parts[0] / parts[1]
    if alt.is_file():
        return alt.read_text(encoding="utf-8")
    return ""


class OperatorHopHarvestFetchBusHandler(BaseHandler):
    step_type = "operator_hop_harvest_fetch_bus_v1"

    @override
    async def execute(self, step: Any, context: Any) -> StepOutput:
        outputs = getattr(context, "step_outputs", {}) or {}
        resolved = step_output_json(outputs, "resolve")
        ledger = step_output_json(outputs, "fetch_ledger")
        worker = str(resolved.get("worker_thread_id") or "")
        summoning = str(resolved.get("summoning_thread_id") or "")
        closeout_turn = ledger.get("closeout_turn")
        closeout_body = str(ledger.get("closeout_body") or "")
        if isinstance(closeout_turn, int):
            turn = await bus_fetch_turn(thread=worker, turn_number=closeout_turn)
            if turn and isinstance(turn.get("body"), str):
                closeout_body = turn["body"]
        harvest_body = ""
        if isinstance(closeout_turn, int):
            for tn in range(closeout_turn + 1, closeout_turn + 20):
                turn = await bus_fetch_turn(thread=worker, turn_number=tn)
                if turn is None:
                    break
                from_agent = str(turn.get("from") or turn.get("from_agent") or "")
                if from_agent.replace("_", "-") in ("web-anthropic", "web_anthropic"):
                    harvest_body = str(turn.get("body") or "")
                    break
        if closeout_body.strip().startswith("{") and not closeout_body.count("stop:"):
            try:
                data = json.loads(closeout_body)
                if isinstance(data, dict):
                    source_ref = data.get("source_ref")
                    if isinstance(source_ref, str):
                        prose = _read_workspaces_uri(source_ref)
                        if prose:
                            closeout_body = prose
            except json.JSONDecodeError:
                pass
        thread_meta: dict[str, object] = {}
        if worker:
            payload, status = await bus_get(f"/threads/{worker}")
            if status < 400:
                thread_meta["worker"] = payload
        summoning_admit_turn = 0
        if summoning:
            payload, _ = await bus_get(f"/threads/{summoning}")
            if isinstance(payload, dict):
                thread_meta["summoning"] = payload
            for tn in range(1, 30):
                turn = await bus_fetch_turn(thread=summoning, turn_number=tn)
                if turn is None:
                    break
                subj = str(turn.get("subject") or "")
                if "admitted" in subj.lower() or "generate" in subj.lower():
                    summoning_admit_turn = tn
        out = {
            "closeout_turn_body": closeout_body,
            "harvest_turn_body": harvest_body,
            "summoning_admit_turn": summoning_admit_turn,
            "thread_meta": thread_meta,
            "closeout_turn": closeout_turn,
        }
        return StepOutput(raw=json.dumps(out), json=out)
