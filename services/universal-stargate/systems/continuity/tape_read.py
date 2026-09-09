"""Relay agent-bus tape render into a ``ContinuityMessagesEnvelope``."""

from __future__ import annotations

import os
from typing import Any

import httpx
from continuity_tape.events import stargate_continuity_tape_read_served
from continuity_tape.messages import (
    EnvelopeMeta,
    ContinuityMessagesEnvelope,
    envelope_wire_dict,
    messages_sha256,
)
from transport_utils import DEFAULT_AGENT_BUS_URL, make_async_client
from universal_logging import get_logger

logger = get_logger(__name__)

_HTTP_TIMEOUT_S = 30.0


def _bus_headers() -> dict[str, str]:
    token = os.getenv("AGENT_BUS_TOKEN", "").strip()
    return {"Authorization": f"Bearer {token}"} if token else {}


def _agent_surface(agent: str | None) -> str:
    if not agent:
        return "mixed"
    if agent == "cursor":
        return "cursor"
    if agent in {"web-anthropic", "claude-web"}:
        return "claude_ai"
    if agent.startswith("grok"):
        return "grok"
    return "mixed"


def _build_sources(segments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    sources: list[dict[str, Any]] = []
    seen: set[str] = set()
    for seg in segments:
        sid = str(seg.get("session_id") or "")
        if not sid or sid in seen:
            continue
        seen.add(sid)
        sources.append(
            {
                "session_id": sid,
                "transcript_id": str(seg.get("transcript_id") or ""),
                "surface": _agent_surface(str(seg.get("dominant_lane") or "")),
                "verbatim_codec": "md-v1",
                "binding": str(seg.get("binding") or ""),
            }
        )
    return sources


def _checkpoint_turns(cells: list[dict[str, Any]]) -> list[int]:
    turns: list[int] = []
    for cell in cells:
        bus_turn_id = cell.get("bus_turn_id")
        if bus_turn_id is None:
            continue
        turn = int(bus_turn_id)
        if turn not in turns:
            turns.append(turn)
    return sorted(turns)


def _codec_counts(segments: list[dict[str, Any]]) -> dict[str, int]:
    counts = {"md-v1": 0, "messages-v1": 0}
    for seg in segments:
        codec = str(seg.get("verbatim_codec") or "md-v1")
        if codec not in counts:
            counts[codec] = 0
        counts[codec] += 1
    if counts["md-v1"] == 0 and counts["messages-v1"] == 0 and segments:
        counts["md-v1"] = len(segments)
    return counts


def _surfaces(segments: list[dict[str, Any]]) -> list[str]:
    out: list[str] = []
    for seg in segments:
        surface = _agent_surface(str(seg.get("dominant_lane") or ""))
        if surface != "mixed" and surface not in out:
            out.append(surface)
    return out or ["mixed"]


def build_envelope_from_tape(
    tape: dict[str, Any],
    *,
    request: dict[str, Any],
    caller_agent: str,
    door: str = "sync",
    execution_id: str | None = None,
) -> ContinuityMessagesEnvelope:
    messages = tape.get("messages") or []
    index = tape.get("index") or []
    open_line = tape.get("open_line") if isinstance(tape.get("open_line"), dict) else {}
    segments = tape.get("segments") or []
    cells = tape.get("cells") or []
    meta_block = tape.get("meta") if isinstance(tape.get("meta"), dict) else {}
    sha = str(meta_block.get("messages_sha256") or messages_sha256(messages))
    meta = EnvelopeMeta(
        surface="mixed",
        tools=request.get("tools", "none"),
        tools_available=bool(meta_block.get("tools_available", False)),
        extras=bool(request.get("include_extras", False)),
        turn_count=int(tape.get("turn_count") or open_line.get("turn_count") or 0),
        message_count=len(messages),
        truncated=bool(tape.get("truncated") or open_line.get("truncated")),
        messages_sha256=sha,
        budget_bytes=int(open_line.get("budget_bytes") or request.get("budget_bytes") or 0),
        payload_bytes=int(open_line.get("payload_bytes") or 0),
        codec_counts=_codec_counts(segments),
        surfaces=_surfaces(segments),
        checkpoint_turns=_checkpoint_turns(cells),
        sources=_build_sources(segments),
        request=request,
        door=door,  # type: ignore[arg-type]
        execution_id=execution_id,
    )
    return ContinuityMessagesEnvelope(
        messages=messages,
        index=index,
        meta=meta,
        open_line=open_line or None,
    )


async def fetch_tape_envelope(
    thread: str,
    *,
    scope: str = "full",
    include_extras: bool = False,
    tools: str = "none",
    budget_bytes: int = 512_000,
    harvest: bool = False,
    caller_agent: str = "stargate",
    door: str = "sync",
    execution_id: str | None = None,
) -> tuple[dict[str, Any], int]:
    """GET agent-bus tape and return ``(wire_dict, http_status)``."""
    request = {
        "thread": thread,
        "scope": scope,
        "include_extras": include_extras,
        "tools": tools,
        "budget_bytes": budget_bytes,
        "harvest": harvest,
    }
    params = {
        "scope": scope,
        "include_extras": str(include_extras).lower(),
        "tools": tools,
        "budget_bytes": budget_bytes,
        "harvest": str(harvest).lower(),
    }
    url = f"/threads/{thread}/tape"
    bus_url = DEFAULT_AGENT_BUS_URL
    try:
        async with make_async_client(bus_url, timeout=_HTTP_TIMEOUT_S) as client:
            resp = await client.get(url, params=params, headers=_bus_headers())
    except httpx.ConnectError as exc:
        logger.warning("agent-bus unreachable for tape-read: %s", exc)
        return (
            {
                "error": {
                    "code": "agent_bus_unreachable",
                    "message": f"agent-bus not reachable: {exc}",
                }
            },
            503,
        )
    except httpx.HTTPError as exc:
        logger.warning("agent-bus tape-read HTTP error: %s", exc)
        return (
            {"error": {"code": "agent_bus_error", "message": str(exc)}},
            503,
        )

    if resp.status_code >= 400:
        try:
            detail = resp.json()
        except ValueError:
            detail = {"error": {"code": f"http_{resp.status_code}", "message": resp.text[:500]}}
        if isinstance(detail, dict) and "error" not in detail and "detail" in detail:
            inner = detail["detail"]
            if isinstance(inner, dict):
                code = inner.get("code") or inner.get("reason") or f"http_{resp.status_code}"
                return (
                    {"error": {"code": str(code), "message": str(inner.get("error") or inner)}},
                    resp.status_code,
                )
        return detail if isinstance(detail, dict) else {"error": detail}, resp.status_code

    tape = resp.json()
    envelope = build_envelope_from_tape(
        tape,
        request=request,
        caller_agent=caller_agent,
        door=door,
        execution_id=execution_id,
    )
    stargate_continuity_tape_read_served(
        thread=thread,
        scope=scope,
        tools=tools,
        include_extras=include_extras,
        message_count=len(envelope.messages),
        index_count=len(envelope.index),
        payload_bytes=int(envelope.meta.payload_bytes or 0),
        truncated=envelope.meta.truncated,
        caller_agent=caller_agent,
        door=door,
        execution_id=execution_id,
    )
    return envelope_wire_dict(envelope), 200


__all__ = ["fetch_tape_envelope", "build_envelope_from_tape", "envelope_wire_dict"]
