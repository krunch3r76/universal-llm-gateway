"""One FACT per (house, n) on the verify lane."""

from __future__ import annotations

from typing import Any, override

from systems.pipeline.core.handlers.builtin import BaseHandler
from systems.pipeline.core.handlers.protocol import StepOutput

from ._clients import bus_get, bus_send, step_output_json
from .admit import fact_subject


def _fact_body(
    options: dict[str, Any],
    card: dict[str, Any],
    envelope_step: dict[str, Any],
) -> str:
    card_uri = str(card.get("uri") or options.get("card_uri") or "")
    card_sha = str(card.get("written_sha256") or "")
    if envelope_step.get("snapshot_uri"):
        snap = (
            f"{envelope_step.get('snapshot_uri')}@"
            f"{envelope_step.get('sha256')}"
        )
        prov = str(envelope_step.get("provenance") or "")
    else:
        snap = "none"
        prov = "none"
    trigger = str(options.get("trigger") or "")
    cap = envelope_step.get("capture")
    if isinstance(cap, dict):
        pointer = f"{cap.get('kind')}@{cap.get('sha256')}"
        if cap.get("pseudo_byte_exact"):
            pointer += " · pseudo_byte_exact"
    else:
        pointer = "none"
    return (
        f"card {card_uri}@{card_sha} trigger {trigger} snapshot {snap} "
        f"provenance {prov} capture {pointer}"
    )


async def run_fact(
    options: dict[str, Any],
    card: dict[str, Any],
    envelope_step: dict[str, Any],
    *,
    get: Any = None,
    send: Any = None,
) -> dict[str, Any]:
    get = get or bus_get
    send = send or bus_send
    subject = fact_subject(options)
    lane = str(options.get("verify_lane") or "")
    listing, status = await get(f"/threads/{lane}/turns")
    turns = listing.get("turns") if isinstance(listing, dict) else []
    body = _fact_body(options, card, envelope_step)
    if status < 400 and isinstance(turns, list):
        for turn in turns:
            if (
                turn.get("subject") == subject
                and not turn.get("superseded")
                and str(turn.get("body") or "") == body
            ):
                return {
                    "refused": None,
                    "skipped": True,
                    "subject": subject,
                    "turn": turn.get("turn_number"),
                    "body": body,
                }
    payload, send_status = await send(
        thread=lane,
        from_agent=str(options.get("bus_identity") or ""),
        to_agent=str(options.get("verifier") or ""),
        subject=subject,
        body=body,
        after_turn=None,
    )
    if send_status >= 400:
        return {"refused": {"code": "fact.bus", "message": str(payload)}}
    return {
        "refused": None,
        "skipped": False,
        "subject": subject,
        "turn": payload.get("turn_number") if isinstance(payload, dict) else None,
        "body": body,
    }


class GrokbotCheckpointFactHandler(BaseHandler):
    step_type = "grokbot_checkpoint_fact_v1"

    @override
    async def execute(self, step: Any, context: Any) -> StepOutput:
        options = dict(getattr(context, "options", {}) or {})
        outputs = getattr(context, "outputs", {}) or {}
        payload = await run_fact(
            options,
            step_output_json(outputs, "card"),
            step_output_json(outputs, "envelope"),
        )
        return StepOutput(raw="", json=payload)
