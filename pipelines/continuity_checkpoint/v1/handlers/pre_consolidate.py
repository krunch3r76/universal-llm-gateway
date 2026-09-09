"""Pre-consolidate via cursor-sdk contract=none read-only dispatch."""

from __future__ import annotations

import json
import logging
import time
from typing import Any, override

from continuity_tape.events import stargate_continuity_checkpoint_card_patched
from systems.pipeline.core.handlers.builtin import BaseHandler
from systems.pipeline.core.handlers.protocol import StepOutput

from ._card_patch import apply_card_patch, parse_worker_json, validate_worker_payload
from ._clients import bus_get, bus_wait, cortex_dispatch, stargate_post, step_output_json
from ._packet import render_pre_consolidate_packet, write_packet_file

logger = logging.getLogger(__name__)

_IDLE_GIVE_UP_S = 20 * 60
_MECHANICAL_PREFIX = "TYPE: CHECKPOINT · pipeline · seal facts"


def _mechanical_residue(seal: dict[str, Any]) -> str:
    session_id = seal.get("session_id") or "unknown"
    turn_count = seal.get("turn_count") or 0
    return (
        f"{_MECHANICAL_PREFIX}\n"
        f"Mission: resume continuity house after seal of {session_id} "
        f"(turns@cp={turn_count}).\n"
    )


def _tape_summary(tape_json: dict[str, Any] | None) -> str:
    if not tape_json:
        return "(tape unavailable)"
    envelope = tape_json.get("envelope") or tape_json
    meta = envelope.get("meta") if isinstance(envelope, dict) else {}
    if not isinstance(meta, dict):
        meta = {}
    return (
        f"messages={meta.get('message_count', '?')} "
        f"truncated={meta.get('truncated', '?')} "
        f"turn_count={meta.get('turn_count', '?')}"
    )


class ContinuityCheckpointPreConsolidateHandler(BaseHandler):
    """Dispatch read-only cursor-sdk packet and apply card patch from worker JSON."""

    step_type = "continuity_checkpoint_pre_consolidate_v1"

    @override
    async def execute(self, step: Any, context: Any) -> StepOutput:
        options: dict[str, Any] = getattr(context, "options", {}) or {}
        thread = str(options.get("thread") or context.dispatch_thread_id or "")
        from_agent = str(options.get("from_agent") or "continuity")
        execution_id = str(context.execution_id or "")
        seat_residue = str(options.get("residue") or "")

        outputs = getattr(context, "outputs", {}) or {}
        seal = step_output_json(outputs, "seal")
        tape_step = step_output_json(outputs, "tape")

        hub_summary = ""
        hub = await cortex_dispatch(
            "entity_get",
            {"entity_id": f"document:{thread}-continuity", "intent": "full"},
        )
        if isinstance(hub, dict) and hub.get("entity"):
            entity = hub["entity"]
            hub_summary = str(entity.get("description") or entity.get("name") or "")

        resume_open = ""
        pools_section = ""
        card_path = f"notes/system/threads/{thread}-continuity.md"
        try:
            import os
            from pathlib import Path

            root_env = os.environ.get("CORTEX_FILES_ROOT")
            if root_env:
                card_file = Path(root_env) / card_path
                if card_file.is_file():
                    card_text = card_file.read_text(encoding="utf-8")
                    from markdown_sections import read_section

                    try:
                        resume_open = read_section(card_text, "Resume open")
                    except Exception:  # noqa: BLE001
                        resume_open = ""
                    try:
                        pools_section = read_section(card_text, "Pools")
                    except Exception:  # noqa: BLE001
                        pools_section = ""
        except Exception:  # noqa: BLE001
            pass

        tip_residue = ""
        tip, _status = await bus_get(
            f"/turns/by-number",
            params={"thread": thread, "turn_number": "latest"},
        )
        if isinstance(tip, dict):
            tip_residue = str(tip.get("body") or "")[:1200]

        if seat_residue:
            result = {
                "residue": seat_residue[:800],
                "residue_source": "seat",
                "mission": "",
                "card_patch_applied": False,
                "executor": "cursor-sdk",
                "worker_thread": None,
                "dispatch_id": None,
            }
            packet = render_pre_consolidate_packet(
                thread=thread,
                seal=seal,
                tape_summary=_tape_summary(tape_step),
                tip_residue=tip_residue,
                resume_open=resume_open,
                pools_section=pools_section,
                hub_summary=hub_summary,
                seat_residue=seat_residue,
            )
            packet_path = write_packet_file(
                thread=thread, execution_id=execution_id, content=packet
            )
            dispatch_body: dict[str, Any] = {
                "op": "generate",
                "seat": "cursor-sdk",
                "contract": "none",
                "lane": "A",
                "read_only": True,
                "mcp": False,
                "server_tools": False,
                "dispatch_thread_id": thread,
                "caller_agent": from_agent,
            }
            if packet_path:
                dispatch_body["packet_path"] = packet_path
            else:
                dispatch_body["prompt"] = packet
            await stargate_post("/api/v1/team/dispatch", dispatch_body)
            return StepOutput(raw=json.dumps(result), json=result)

        packet = render_pre_consolidate_packet(
            thread=thread,
            seal=seal,
            tape_summary=_tape_summary(tape_step),
            tip_residue=tip_residue,
            resume_open=resume_open,
            pools_section=pools_section,
            hub_summary=hub_summary,
            seat_residue="",
        )
        packet_path = write_packet_file(
            thread=thread, execution_id=execution_id, content=packet
        )
        dispatch_body = {
            "op": "generate",
            "seat": "cursor-sdk",
            "contract": "none",
            "lane": "A",
            "read_only": True,
            "mcp": False,
            "server_tools": False,
            "dispatch_thread_id": thread,
            "caller_agent": from_agent,
        }
        if packet_path:
            dispatch_body["packet_path"] = packet_path
        else:
            dispatch_body["prompt"] = packet

        dispatch_resp, dispatch_status = await stargate_post(
            "/api/v1/team/dispatch", dispatch_body
        )
        if dispatch_status >= 400 or dispatch_resp.get("error"):
            result = {
                "residue": _mechanical_residue(seal),
                "residue_source": "mechanical",
                "mission": "",
                "card_patch_applied": False,
                "executor": "cursor-sdk",
                "worker_thread": None,
                "dispatch_id": None,
            }
            return StepOutput(raw=json.dumps(result), json=result)

        worker_thread = str(
            dispatch_resp.get("thread")
            or dispatch_resp.get("thread_id")
            or dispatch_resp.get("worker_thread")
            or dispatch_resp.get("dispatch_thread_id")
            or thread
        )
        dispatch_id = dispatch_resp.get("dispatch_id")
        poll_hint = dispatch_resp.get("poll_hint") or {}
        poll_args = poll_hint.get("arguments") if isinstance(poll_hint, dict) else {}
        if not isinstance(poll_args, dict):
            poll_args = {}
        after_turn = int(
            dispatch_resp.get("after_turn")
            or poll_args.get("after_turn")
            or 0
        )
        reply_from = str(
            dispatch_resp.get("reply_from_agent")
            or poll_args.get("from_agent")
            or "cursor-sdk"
        )

        last_progress = time.monotonic()
        last_seen_turn = after_turn
        worker_body = ""
        while True:
            wait_resp, _ = await bus_wait(
                thread=worker_thread,
                after_turn=after_turn,
                wait_seconds=60.0,
                from_agent=reply_from,
            )
            complete = bool(wait_resp.get("complete"))
            turns = wait_resp.get("turns") or []
            for turn in turns:
                tn = int(turn.get("turn_number") or 0)
                if tn > last_seen_turn:
                    last_seen_turn = tn
                    last_progress = time.monotonic()
                    if str(turn.get("from_agent") or "") == reply_from:
                        worker_body = str(turn.get("body") or "")
            if complete and worker_body:
                break
            if time.monotonic() - last_progress >= _IDLE_GIVE_UP_S:
                break

        worker_data = parse_worker_json(worker_body) if worker_body else None
        if not worker_data:
            result = {
                "residue": _mechanical_residue(seal),
                "residue_source": "mechanical",
                "mission": "",
                "card_patch_applied": False,
                "executor": "cursor-sdk",
                "worker_thread": worker_thread,
                "dispatch_id": dispatch_id,
            }
            return StepOutput(raw=json.dumps(result), json=result)

        ok, reason = validate_worker_payload(worker_data)
        if not ok:
            result = {
                "residue": _mechanical_residue(seal),
                "residue_source": "mechanical",
                "mission": "",
                "card_patch_applied": False,
                "executor": "cursor-sdk",
                "worker_thread": worker_thread,
                "dispatch_id": dispatch_id,
                "validation": reason,
            }
            return StepOutput(raw=json.dumps(result), json=result)

        card_patch = worker_data.get("card_patch") or {}
        applied, card_uri, patch_reason = apply_card_patch(
            thread=thread,
            resume_open=str(card_patch.get("resume_open") or ""),
            opportunities_rows=list(card_patch.get("opportunities_rows") or []),
        )
        if applied:
            stargate_continuity_checkpoint_card_patched(
                execution_id=execution_id,
                thread=thread,
                surface=str(options.get("surface") or "cursor"),
                from_agent=from_agent,
                card_uri=card_uri,
                executor="cursor-sdk",
            )

        residue = str(worker_data.get("residue") or "")[:800]
        mission = str(worker_data.get("mission") or "")
        result = {
            "residue": residue,
            "residue_source": "model",
            "mission": mission,
            "card_patch_applied": applied,
            "card_patch_reason": patch_reason,
            "executor": "cursor-sdk",
            "worker_thread": worker_thread,
            "dispatch_id": dispatch_id,
        }
        return StepOutput(raw=json.dumps(result), json=result)
