"""CHECKPOINT turn on the house root. Reuses continuity post footer."""

from __future__ import annotations

from typing import Any, override

from agent_bus_store.checkpoint_projection import CANONICAL_RESUME_FOOTER
from systems.pipeline.core.handlers.builtin import BaseHandler
from systems.pipeline.core.handlers.protocol import StepOutput

from ._clients import bus_get, bus_send, step_output_json

_MARKER = "house-cp:"


def compose_checkpoint_body(
    *,
    residue: str,
    agent_id: str,
    turn_count: int,
    coverage: str,
    channel: str,
    session_id: str,
    messages_sha256: str,
    surface: str,
    provenance: str,
    producer: str,
    house: str,
    n: int,
) -> str:
    hop = " · channel=hop" if channel == "hop" else ""
    lines = [
        "## Residue",
        residue.strip(),
        f"{_MARKER}{house}:{n}",
        "",
        "## Anchor",
        (
            f"Window: agent_id={agent_id} · turns@cp={turn_count} · "
            f"coverage={coverage}{hop}"
        ),
        (
            f"Harvest: transcript:{session_id} · messages_sha256:{messages_sha256} · "
            f"codec:messages-v1 · surface:{surface} · provenance:{provenance} · "
            f"producer:{producer}"
        ),
        "",
        CANONICAL_RESUME_FOOTER,
    ]
    return "\n".join(lines)


def _duplicate(turns: list[dict[str, Any]], house: str, n: int) -> bool:
    token = f"{_MARKER}{house}:{n}"
    return any(token in str(t.get("body") or "") for t in turns)


async def run_post(
    options: dict[str, Any],
    seal: dict[str, Any],
    envelope_step: dict[str, Any],
    *,
    get: Any = None,
    send: Any = None,
) -> dict[str, Any]:
    get = get or bus_get
    send = send or bus_send
    thread = str(options.get("root_thread") or "")
    house = str(options["house"])
    n = int(options["n"])
    listing, status = await get(f"/threads/{thread}/turns")
    turns = listing.get("turns") if isinstance(listing, dict) else None
    if status < 400 and isinstance(turns, list) and _duplicate(turns, house, n):
        return {"refused": None, "duplicate": True, "body": None, "turn": None}
    body = compose_checkpoint_body(
        residue=str(options.get("residue") or ""),
        agent_id=str(options.get("agent_id") or ""),
        turn_count=int(envelope_step.get("turn_count") or 0),
        coverage=str(envelope_step.get("coverage") or ""),
        channel=str(options.get("channel") or "cp"),
        session_id=str(seal.get("session_id") or ""),
        messages_sha256=str(envelope_step.get("messages_sha256") or ""),
        surface=str(envelope_step.get("surface") or ""),
        provenance=str(envelope_step.get("provenance") or ""),
        producer=str(envelope_step.get("producer") or ""),
        house=house,
        n=n,
    )
    payload, send_status = await send(
        thread=thread,
        from_agent=str(options.get("bus_identity") or ""),
        to_agent=str(options.get("verifier") or ""),
        subject="CHECKPOINT",
        body=body,
        after_turn=None,
    )
    if send_status >= 400:
        return {
            "refused": {
                "code": "post.bus",
                "message": str(payload),
            }
        }
    return {
        "refused": None,
        "duplicate": False,
        "body": body,
        "turn": payload.get("turn_number") if isinstance(payload, dict) else None,
    }


class GrokbotCheckpointPostHandler(BaseHandler):
    step_type = "grokbot_checkpoint_post_v1"

    @override
    async def execute(self, step: Any, context: Any) -> StepOutput:
        options = dict(getattr(context, "options", {}) or {})
        outputs = getattr(context, "outputs", {}) or {}
        payload = await run_post(
            options,
            step_output_json(outputs, "seal"),
            step_output_json(outputs, "envelope"),
        )
        return StepOutput(raw="", json=payload)
