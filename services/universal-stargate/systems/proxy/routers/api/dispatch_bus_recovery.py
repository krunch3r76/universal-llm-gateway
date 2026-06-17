"""Recover pipeline execution state from durable agent-bus dispatch links.

When the in-memory tracker and sqlite journal both miss after a restart, the
dispatch link row (and optional closeout turn on the thread) still holds enough
signal to synthesize a recovered GET /pipelines/executions/{id} payload.

B-middle: optional ``wait_seconds`` blocks on agent-bus wait for SDK closeout
turns and attaches the closeout body to ``result``.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

import httpx
from transport_utils import make_async_client
from universal_logging import get_logger

logger = get_logger(__name__)

_HTTP_TIMEOUT_S = 10.0
_WAIT_CHUNK_SECONDS = 60.0
_CURSOR_SDK_REPLY_SEAT = "cursor-sdk"


def _infer_terminal_status(subject: str) -> str:
    return "failed" if "FAILED" in subject else "completed"


def _sdk_terminal_turn(turns: list[dict[str, Any]]) -> dict[str, Any] | None:
    for turn in turns:
        author = turn.get("from_agent") or turn.get("from")
        if author != "cursor-sdk":
            continue
        subject = turn.get("subject") or ""
        if subject.startswith("cursor-sdk dispatch"):
            return turn
    return None


def _build_recovered_record(
    *,
    execution_id: str,
    pipeline_id: str,
    thread_id: str,
    status: str,
    completed_at: str | None,
    result: str | None = None,
) -> dict[str, Any]:
    return {
        "execution_id": execution_id,
        "pipeline": pipeline_id,
        "status": status,
        "started_at": None,
        "completed_at": completed_at,
        "result": result,
        "error": None,
        "caller_agent": None,
        "output_contract": "thread",
        "target_thread": thread_id,
        "op": "generate",
        "thread_reply_observed_at": completed_at,
        "delivery": None,
        "recovered_from": "bus_thread",
    }


async def _fetch_turn_body(
    client: httpx.AsyncClient,
    *,
    thread_id: str,
    turn_number: int,
    headers: dict[str, str],
) -> str | None:
    turn_resp = await client.get(
        "/turns/by-number",
        params={"thread": thread_id, "turn_number": turn_number},
        headers=headers,
    )
    if turn_resp.status_code >= 400:
        return None
    body = turn_resp.json().get("body")
    return body if isinstance(body, str) else None


async def _closeout_body_for_turn(
    client: httpx.AsyncClient,
    *,
    thread_id: str,
    closeout: dict[str, Any],
    headers: dict[str, str],
) -> str | None:
    inline = closeout.get("body")
    if isinstance(inline, str):
        return inline
    turn_number = closeout.get("turn_number")
    if isinstance(turn_number, int):
        return await _fetch_turn_body(
            client,
            thread_id=thread_id,
            turn_number=turn_number,
            headers=headers,
        )
    return None


async def await_bus_closeout_reply(
    client: httpx.AsyncClient,
    *,
    thread_id: str,
    wait_seconds: float,
    headers: dict[str, str],
    after_turn: int = 1,
    from_agent: str = _CURSOR_SDK_REPLY_SEAT,
) -> str | None:
    """Block on agent-bus wait until an SDK closeout turn appears."""
    if wait_seconds <= 0:
        return None
    deadline = time.monotonic() + wait_seconds
    while time.monotonic() < deadline:
        remaining = max(0.0, deadline - time.monotonic())
        wait_s = min(_WAIT_CHUNK_SECONDS, remaining)
        if wait_s <= 0:
            break
        try:
            resp = await client.get(
                f"/threads/{thread_id}/wait",
                params={
                    "after_turn": after_turn,
                    "wait": wait_s,
                    "completion": "first_reply_from",
                    "from_agent": from_agent,
                },
                headers=headers,
            )
        except httpx.HTTPError as exc:
            logger.warning(
                "dispatch recovery wait transport error thread=%s: %s",
                thread_id,
                exc,
            )
            await asyncio.sleep(1.0)
            continue
        if resp.status_code >= 400:
            logger.warning(
                "dispatch recovery wait rejected thread=%s status=%s",
                thread_id,
                resp.status_code,
            )
            return None
        snap = resp.json()
        if not snap.get("complete"):
            continue
        reply_turn = snap.get("qualifying_reply_turn")
        if not isinstance(reply_turn, int):
            return None
        return await _fetch_turn_body(
            client,
            thread_id=thread_id,
            turn_number=reply_turn,
            headers=headers,
        )
    return None


async def recover_execution_from_bus_thread(
    execution_id: str,
    *,
    url: str,
    auth_token: str,
    wait_seconds: float = 0.0,
) -> dict[str, Any] | None:
    """Return a synthesized recovered record, or None when no bus signal exists."""
    if not auth_token:
        return None
    headers = {"Authorization": f"Bearer {auth_token}"}
    try:
        async with make_async_client(url, timeout=_HTTP_TIMEOUT_S) as client:
            link_resp = await client.get(
                f"/dispatch-links/{execution_id}",
                headers=headers,
            )
            if link_resp.status_code == 404:
                return None
            if link_resp.status_code != 200:
                logger.warning(
                    "dispatch-link lookup for execution_id=%s returned %s",
                    execution_id,
                    link_resp.status_code,
                )
                return None
            link = link_resp.json()
            thread_id = link["thread_id"]
            pipeline_id = link["pipeline_id"]
            terminal_status = link.get("terminal_status")
            terminal_at = link.get("terminal_at")

            if terminal_status in {"completed", "failed"}:
                completed_at = terminal_at if isinstance(terminal_at, str) else None
                closeout_body: str | None = None
                turns_resp = await client.get(
                    "/turns",
                    params={"thread": thread_id, "last": 50},
                    headers=headers,
                )
                if turns_resp.status_code == 200:
                    turns = turns_resp.json().get("turns") or []
                    closeout = _sdk_terminal_turn(turns)
                    if closeout is not None:
                        closeout_body = await _closeout_body_for_turn(
                            client,
                            thread_id=thread_id,
                            closeout=closeout,
                            headers=headers,
                        )
                return _build_recovered_record(
                    execution_id=execution_id,
                    pipeline_id=pipeline_id,
                    thread_id=thread_id,
                    status=terminal_status,
                    completed_at=completed_at,
                    result=closeout_body,
                )

            turns_resp = await client.get(
                "/turns",
                params={"thread": thread_id, "last": 50},
                headers=headers,
            )
            if turns_resp.status_code != 200:
                return None
            turns = turns_resp.json().get("turns") or []
            closeout = _sdk_terminal_turn(turns)
            closeout_body = None
            if closeout is not None:
                closeout_body = await _closeout_body_for_turn(
                    client,
                    thread_id=thread_id,
                    closeout=closeout,
                    headers=headers,
                )

            if closeout is None and wait_seconds > 0:
                closeout_body = await await_bus_closeout_reply(
                    client,
                    thread_id=thread_id,
                    wait_seconds=wait_seconds,
                    headers=headers,
                )
                if closeout_body is not None:
                    turns_resp = await client.get(
                        "/turns",
                        params={"thread": thread_id, "last": 50},
                        headers=headers,
                    )
                    if turns_resp.status_code == 200:
                        turns = turns_resp.json().get("turns") or []
                        closeout = _sdk_terminal_turn(turns)

            if closeout is None:
                return _build_recovered_record(
                    execution_id=execution_id,
                    pipeline_id=pipeline_id,
                    thread_id=thread_id,
                    status="running",
                    completed_at=None,
                )
            status = _infer_terminal_status(str(closeout.get("subject") or ""))
            return _build_recovered_record(
                execution_id=execution_id,
                pipeline_id=pipeline_id,
                thread_id=thread_id,
                status=status,
                completed_at=closeout.get("created_at"),
                result=closeout_body,
            )
    except httpx.HTTPError as exc:
        logger.warning(
            "Bus-thread recovery transport error for execution_id=%s: %s",
            execution_id,
            exc,
        )
        return None
