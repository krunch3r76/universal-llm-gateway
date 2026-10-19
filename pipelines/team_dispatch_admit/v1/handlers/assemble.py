"""Assemble step — final AdmitPlan (skill_rows always empty in v1)."""

from __future__ import annotations

import json
from typing import Any, override

from systems.pipeline.core.handlers.builtin import BaseHandler
from systems.pipeline.core.handlers.protocol import StepOutput


def _turn0_budget_from_plan(plan: dict[str, Any]) -> dict[str, Any]:
    packet = str(plan.get("packet_markdown") or "")
    guidance = ""
    if "<task_guidance>" in packet:
        start = packet.index("<task_guidance>") + len("<task_guidance>")
        end = packet.index("</task_guidance>", start)
        guidance = packet[start:end]
    scoreboard_embed = guidance
    packet_bytes = len(packet.encode("utf-8"))
    task_guidance_bytes = len(guidance.encode("utf-8"))
    scoreboard_embed_bytes = len(scoreboard_embed.encode("utf-8"))
    preamble_bytes = max(0, packet_bytes - task_guidance_bytes)
    total = packet_bytes
    return {
        "packet_bytes": packet_bytes,
        "task_guidance_bytes": task_guidance_bytes,
        "scoreboard_embed_bytes": scoreboard_embed_bytes,
        "injected_invariant_bytes": 0,
        "preamble_bytes": preamble_bytes,
        "total_turn0_bytes": total,
        "delegation_tier": plan.get("selected_seat_role"),
        "invariant_delivery_mode": "inline",
        "scoreboard_tip_sha": plan.get("scoreboard_tip_sha"),
        "policy_sha256": plan.get("policy_sha256"),
        "policy_epoch": plan.get("policy_epoch"),
    }


def evaluate_assemble(
    validate_out: dict[str, Any], defaults_out: dict[str, Any]
) -> dict[str, Any]:
    for prior in (validate_out, defaults_out):
        if prior.get("admitted") is False:
            return dict(prior)
    plan = dict(defaults_out)
    plan["skill_rows"] = []
    plan["admitted"] = True
    if plan.get("job") == "conductor" and plan.get("packet_markdown"):
        plan["turn0_budget"] = _turn0_budget_from_plan(plan)
    return plan


class TeamDispatchAdmitAssembleHandler(BaseHandler):
    step_type = "team_dispatch_admit_assemble_v1"

    @override
    async def execute(self, step: Any, context: Any) -> StepOutput:
        validate_out = _step_json(context, "validate")
        defaults_out = _step_json(context, "defaults")
        payload = evaluate_assemble(validate_out, defaults_out)
        return StepOutput(raw=json.dumps(payload), json=payload)


def _step_json(context: Any, name: str) -> dict[str, Any]:
    outputs = getattr(context, "outputs", {}) or {}
    step_out = outputs.get(name)
    json_out = getattr(step_out, "json", None) if step_out is not None else None
    return json_out if isinstance(json_out, dict) else {}
