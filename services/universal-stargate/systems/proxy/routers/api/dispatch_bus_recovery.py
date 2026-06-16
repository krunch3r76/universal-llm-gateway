"""Recover pipeline execution state from durable agent-bus dispatch links.

When the in-memory tracker and sqlite journal both miss after a restart, the
dispatch link row (and optional closeout turn on the thread) still holds enough
signal to synthesize a recovered GET /pipelines/executions/{id} payload.
"""

from __future__ import annotations

from typing import Any

import httpx
from transport_utils import make_async_client
from universal_logging import get_logger

logger = get_logger(__name__)

_HTTP_TIMEOUT_S = 10.0


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
) -> dict[str, Any]:
    return {
        "execution_id": execution_id,
        "pipeline": pipeline_id,
        "status": status,
        "started_at": None,
        "completed_at": completed_at,
        "result": None,
        "error": None,
        "caller_agent": None,
        "output_contract": "thread",
        "target_thread": thread_id,
        "op": "generate",
        "thread_reply_observed_at": completed_at,
        "delivery": None,
        "recovered_from": "bus_thread",
    }


async def recover_execution_from_bus_thread(
    execution_id: str,
    *,
    url: str,
    auth_token: str,
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
                return _build_recovered_record(
                    execution_id=execution_id,
                    pipeline_id=pipeline_id,
                    thread_id=thread_id,
                    status=terminal_status,
                    completed_at=completed_at,
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
            )
    except httpx.HTTPError as exc:
        logger.warning(
            "Bus-thread recovery transport error for execution_id=%s: %s",
            execution_id,
            exc,
        )
        return None
