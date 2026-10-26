"""Render leg packets when transition.needs_packet."""

from __future__ import annotations

import json
from typing import Any, override

from mechanical_land.packets import (
    check_review_prompt,
    packet_sha256,
    render_evidence_packet,
    render_fix_packet,
    render_review_packet,
    review_request_body,
)
from systems.pipeline.core.handlers.builtin import BaseHandler
from systems.pipeline.core.handlers.protocol import StepOutput


def evaluate_render_packet(
    transition_out: dict[str, Any],
    opts: dict[str, Any],
) -> dict[str, Any]:
    if not transition_out.get("needs_packet"):
        return {"skipped": True, "phase": "render_packet"}
    run_id = str(opts.get("run_id") or "ml-pending")
    step_id = str(transition_out.get("step_id") or "")
    attempt = int(transition_out.get("attempt") or 1)
    reason = str(transition_out.get("reason") or "")
    ctx = opts.get("render_context") or {}
    if not isinstance(ctx, dict):
        ctx = {}
    markdown = ""
    if step_id == "fix":
        markdown = render_fix_packet(
            run_id=run_id,
            step_id=step_id,
            attempt=attempt,
            reason=reason,
            gate_output=ctx.get("gate_output"),
            review_blockers=ctx.get("review_blockers"),
            files_expected=ctx.get("files_expected"),
        )
    elif step_id == "review":
        markdown = render_review_packet(
            run_id=run_id,
            attempt=attempt,
            range_spec=str(ctx.get("range") or "0" * 40 + ".." + "0" * 40),
            head=str(ctx.get("head") or "0" * 40),
            branch=ctx.get("branch"),
            lane_thread=ctx.get("lane_thread"),
            fixes=ctx.get("fixes") if attempt >= 2 else None,
        )
    elif step_id == "evidence":
        markdown = render_evidence_packet(
            run_id=run_id,
            ask=str(opts.get("ask") or ""),
            read_paths=list(opts.get("read_paths") or []),
        )
    sha = packet_sha256(markdown) if markdown else None
    return {
        "phase": "render_packet",
        "packet_markdown": markdown,
        "packet_sha256": sha,
        "review_request_body": review_request_body(markdown) if markdown else "",
        "check_review_prompt": check_review_prompt(markdown) if markdown else "",
    }


class MechanicalLandRenderPacketHandler(BaseHandler):
    step_type = "mechanical_land_render_packet_v1"

    @override
    async def execute(self, step: Any, context: Any) -> StepOutput:
        opts = dict(getattr(context, "options", {}) or {})
        transition_out = _step_json(context, "transition")
        payload = evaluate_render_packet(transition_out, opts)
        return StepOutput(raw=json.dumps(payload), json=payload)


def _step_json(context: Any, name: str) -> dict[str, Any]:
    outputs = getattr(context, "outputs", {}) or {}
    step_out = outputs.get(name)
    json_out = getattr(step_out, "json", None) if step_out is not None else None
    return json_out if isinstance(json_out, dict) else {}
