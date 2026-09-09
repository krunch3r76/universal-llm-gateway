"""Compose CHECKPOINT body and post to agent-bus."""

from __future__ import annotations

import json
import logging
from typing import Any, override

from agent_bus_store.checkpoint_projection import CANONICAL_RESUME_FOOTER
from continuity_tape.events import stargate_continuity_checkpoint_posted
from systems.pipeline.core.handlers.builtin import BaseHandler
from systems.pipeline.core.handlers.protocol import StepOutput

from ._clients import bus_get, bus_send, step_output_json

logger = logging.getLogger(__name__)


def _compose_body(
    *,
    residue: str,
    seal: dict[str, Any],
    mission: str,
) -> str:
    lines = ["## Residue (authored — cap ~800 chars)", residue.strip(), "", "## Anchor"]
    refused = seal.get("refused")
    if refused:
        lines.append(f"Harvest: refused({refused.get('code', 'unknown')})")
    else:
        transcript_id = seal.get("transcript_id") or ""
        turn_count = seal.get("turn_count") or 0
        session_id = seal.get("session_id") or ""
        sha = seal.get("messages_sha256") or ""
        lines.append(f"Window: transcript_id={transcript_id} · turns@cp={turn_count}")
        lines.append(
            f"Harvest: transcript:{session_id} · messages_sha256:{sha} · "
            "codec:messages-v1 · surface:cursor"
        )
    mission_line = mission.strip() or "Mission: resume continuity house from this CHECKPOINT."
    if not mission_line.startswith("Mission:"):
        mission_line = f"Mission: {mission_line}"
    lines.extend(["", mission_line, CANONICAL_RESUME_FOOTER])
    return "\n".join(lines)


class ContinuityCheckpointPostHandler(BaseHandler):
    """Post CHECKPOINT turn with after_turn guard and terminal result dict."""

    step_type = "continuity_checkpoint_post_v1"

    @override
    async def execute(self, step: Any, context: Any) -> StepOutput:
        options: dict[str, Any] = getattr(context, "options", {}) or {}
        thread = str(options.get("thread") or context.dispatch_thread_id or "")
        from_agent = str(options.get("from_agent") or "continuity")
        surface = str(options.get("surface") or "cursor")
        execution_id = str(context.execution_id or "")

        outputs = getattr(context, "outputs", {}) or {}
        seal = step_output_json(outputs, "seal")
        pre = step_output_json(outputs, "pre_consolidate")

        if not pre:
            pre = {
                "residue": (
                    "TYPE: CHECKPOINT · pipeline · seal refused or pre_consolidate skipped.\n"
                    f"Mission: record checkpoint for {thread}.\n"
                ),
                "mission": f"resume {thread}",
                "executor": "cursor-sdk",
                "card_patch_applied": False,
            }

        residue = str(pre.get("residue") or "")[:800]
        mission = str(pre.get("mission") or "")
        body = _compose_body(residue=residue, seal=seal, mission=mission)

        tip, _ = await bus_get(
            "/turns/by-number",
            params={"thread": thread, "turn_number": "latest"},
        )
        after_turn = int(tip.get("turn_number") or 0) if isinstance(tip, dict) else 0

        slug = thread[:12]
        subject = f"CHECKPOINT {slug} {execution_id[:8]}"
        send_resp, send_status = await bus_send(
            thread=thread,
            from_agent=from_agent,
            to_agent="web",
            subject=subject,
            body=body,
            after_turn=after_turn,
        )
        if send_status == 409:
            detail = send_resp.get("detail") or send_resp
            if isinstance(detail, dict):
                latest = int(detail.get("latest_turn_number") or after_turn)
                send_resp, send_status = await bus_send(
                    thread=thread,
                    from_agent=from_agent,
                    to_agent="web",
                    subject=subject,
                    body=body,
                    after_turn=latest,
                )

        if send_status >= 400:
            from continuity_tape.events import stargate_continuity_checkpoint_failed

            code = str(
                (send_resp.get("error") or {}).get("code")
                or send_resp.get("code")
                or f"http_{send_status}"
            )
            stargate_continuity_checkpoint_failed(
                execution_id=execution_id,
                thread=thread,
                surface=surface,
                from_agent=from_agent,
                stage="post",
                code=code,
            )
            payload = {"error": send_resp, "status_code": send_status}
            return StepOutput(raw=json.dumps(payload), json=payload)

        turn_raw = send_resp.get("turn_number") or send_resp.get("turn") or 0
        if isinstance(turn_raw, dict):
            turn_raw = turn_raw.get("turn_number") or turn_raw.get("number") or 0
        bus_turn = int(turn_raw or 0)
        stargate_continuity_checkpoint_posted(
            execution_id=execution_id,
            thread=thread,
            surface=surface,
            from_agent=from_agent,
            bus_turn=bus_turn,
        )

        session_id = seal.get("session_id") or ""
        result = {
            "execution_id": execution_id,
            "thread": thread,
            "surface": surface,
            "status": "posted",
            "bus_turn": bus_turn,
            "pre_consolidate": {
                "executor": pre.get("executor") or "cursor-sdk",
                "card_patch_applied": bool(pre.get("card_patch_applied")),
                "residue_source": pre.get("residue_source"),
            },
            "harvest_entity_id": f"transcript:{session_id}" if session_id else None,
            "seal": {
                "turn_count": seal.get("turn_count"),
                "already_closed": seal.get("already_closed"),
                "refused": seal.get("refused"),
            },
        }
        return StepOutput(raw=json.dumps(result, default=str), json=result)
