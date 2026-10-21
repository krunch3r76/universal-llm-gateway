"""CHECKPOINT turn on the house root. Reuses continuity post footer."""

from __future__ import annotations

from typing import Any, override

from agent_bus_store.checkpoint_projection import CANONICAL_RESUME_FOOTER
from continuity_tape.events_checkpoint import stargate_continuity_checkpoint_posted
from systems.pipeline.core.handlers.builtin import BaseHandler
from systems.pipeline.core.handlers.protocol import StepOutput

from ._clients import bus_get, bus_send, refuse, step_output_json
from ._handoff import handoff_sha12

_MARKER = "house-cp:"


def _format_mission(*, mission: str | None, house: str, n: int) -> str:
    if isinstance(mission, str) and mission.strip():
        text = " ".join(mission.splitlines()).strip()
    else:
        text = f"resume grok house {house} from checkpoint {n}"
    if text.startswith("Mission:"):
        text = text[len("Mission:") :].strip()
    if len(text) > 200:
        text = text[:200]
    return f"Mission: {text}"


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
    capture: str = "none",
    mission: str | None = None,
    handoff: str = "none",
) -> str:
    hop = " · channel=hop" if channel == "hop" else ""
    mission_line = _format_mission(mission=mission, house=house, n=n)
    lines = [
        "## Residue",
        residue.strip(),
        f"{_MARKER}{house}:{n}",
        "",
        "## Anchor",
        (
            f"Window: transcript_id={agent_id} · turns@cp={turn_count} · "
            f"coverage={coverage}{hop}"
        ),
        (
            f"Harvest: transcript:{session_id} · messages_sha256:{messages_sha256} · "
            f"codec:messages-v1 · surface:{surface} · provenance:{provenance} · "
            f"producer:{producer} · capture:{capture} · handoff:{handoff}"
        ),
        "",
        mission_line,
        "",
        CANONICAL_RESUME_FOOTER,
    ]
    return "\n".join(lines)


def _capture_token(envelope_step: dict[str, Any]) -> str:
    cap = envelope_step.get("capture")
    if not isinstance(cap, dict):
        return "none"
    token = f"{cap.get('kind')}@{cap.get('sha256')}"
    if cap.get("pseudo_byte_exact"):
        token += " · pseudo_byte_exact"
    return token


def _duplicate(turns: list[dict[str, Any]], house: str, n: int) -> bool:
    token = f"{_MARKER}{house}:{n}"
    return any(token in str(t.get("body") or "") for t in turns)


def _bus_turn_from_payload(payload: dict[str, Any]) -> int:
    turn_raw = payload.get("turn_number")
    if turn_raw is None and isinstance(payload.get("turn"), dict):
        turn_raw = payload["turn"].get("turn_number")
    return int(turn_raw or 0)


async def run_post(
    options: dict[str, Any],
    seal: dict[str, Any],
    envelope_step: dict[str, Any],
    *,
    get: Any = None,
    send: Any = None,
    execution_id: str = "",
) -> dict[str, Any]:
    if (
        seal.get("_skipped")
        or seal.get("refused")
        or not seal.get("session_id")
        or envelope_step.get("_skipped")
        or not envelope_step.get("snapshot_uri")
    ):
        return refuse("post.upstream_skipped", "seal or envelope missing")
    if envelope_step.get("handoff_binding") == "stale":
        reasons = envelope_step.get("handoff_reasons") or []
        joined = ",".join(str(item) for item in reasons)
        return refuse("post.handoff_stale", joined or "handoff")
    get = get or bus_get
    send = send or bus_send
    thread = str(options.get("root_thread") or "")
    house = str(options["house"])
    n = int(options["n"])
    listing, status = await get("/turns", params={"thread": thread})
    turns = listing.get("turns") if isinstance(listing, dict) else None
    if status < 400 and isinstance(turns, list) and _duplicate(turns, house, n):
        return {"refused": None, "duplicate": True, "body": None, "turn": None}
    body = compose_checkpoint_body(
        residue=str(options.get("residue") or ""),
        agent_id=str(options.get("agent_id") or "").strip(),
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
        capture=_capture_token(envelope_step),
        mission=options.get("mission")
        if isinstance(options.get("mission"), str)
        else None,
        handoff=handoff_sha12(
            (envelope_step.get("envelope") or {}).get("handoff")
            if isinstance(envelope_step.get("envelope"), dict)
            else None
        ),
    )
    tip, tip_status = await get(
        "/turns/by-number",
        params={"thread": thread, "turn_number": "latest"},
    )
    after_turn = (
        int(tip.get("turn_number") or 0)
        if tip_status < 400 and isinstance(tip, dict)
        else 0
    )
    send_kwargs = dict(
        thread=thread,
        from_agent=str(options.get("bus_identity") or ""),
        to_agent=str(options.get("verifier") or ""),
        subject="CHECKPOINT",
        body=body,
        after_turn=after_turn,
    )
    payload, send_status = await send(**send_kwargs)
    if send_status == 409:
        detail = payload.get("detail") or payload
        if isinstance(detail, dict):
            latest = int(detail.get("latest_turn_number") or after_turn)
            send_kwargs["after_turn"] = latest
            payload, send_status = await send(**send_kwargs)
    if send_status >= 400:
        return {
            "refused": {
                "code": "post.bus",
                "message": f"http_{send_status}: {payload}",
            }
        }
    bus_turn = _bus_turn_from_payload(payload if isinstance(payload, dict) else {})
    if bus_turn > 0:
        stargate_continuity_checkpoint_posted(
            execution_id=execution_id,
            thread=thread,
            surface=str(envelope_step.get("surface") or "grok"),
            from_agent=str(options.get("bus_identity") or ""),
            bus_turn=bus_turn,
        )
    channel = str(options.get("channel") or "cp")
    mode = str(options.get("mode") or "")
    if bus_turn > 0 and not seal.get("refused") and (
        channel == "hop" or mode in ("seal", "harvest_seal")
    ):
        from bus_watch.impresario_ear_common import maybe_enqueue_after_hop

        maybe_enqueue_after_hop(
            root_thread=thread,
            departing_transcript_id=str(options.get("agent_id") or seal.get("transcript_id") or ""),
            hop_turn=bus_turn,
            channel="hop" if channel == "hop" else None,
        )
    return {
        "refused": None,
        "duplicate": False,
        "body": body,
        "turn": bus_turn or None,
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
            execution_id=str(getattr(context, "execution_id", "") or ""),
        )
        return StepOutput(raw="", json=payload)
