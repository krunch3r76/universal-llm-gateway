"""Coordination-thread pointers for cursor-sdk generate (result-thread split)."""

from __future__ import annotations

import os
from typing import Any

import httpx
from transport_utils import DEFAULT_AGENT_BUS_URL, make_async_client
from universal_logging import get_logger

logger = get_logger(__name__)

_ADMIT_SUBJECT = "cursor-sdk generate admitted"


def _bus_headers() -> dict[str, str]:
    token = os.getenv("AGENT_BUS_TOKEN", "").strip()
    return {"Authorization": f"Bearer {token}"} if token else {}


def _bus_token_configured() -> bool:
    if os.getenv("AGENT_BUS_TOKEN", "").strip():
        return True
    return os.getenv("ALLOW_UNSET_AGENT_BUS_TOKEN", "").strip().lower() in (
        "1",
        "true",
        "yes",
    )


def _admit_body(*, worker_thread_id: str, contract: str) -> str:
    lines = [
        f"Worker thread `{worker_thread_id}` — poll via `poll_hint` from the 202 "
        "response (not this coordination thread).",
    ]
    if contract == "implement":
        lines.append(
            "contract=implement: deliverable also stages on the bound todo — "
            "entity_get after worker closeout."
        )
    return "\n".join(lines)


async def _post_coord_turn(
    *,
    coord_thread_id: str,
    to_agent: str,
    from_agent: str,
    subject: str,
    body: str,
) -> None:
    if not _bus_token_configured() or not coord_thread_id:
        return
    payload: dict[str, Any] = {
        "thread": coord_thread_id,
        "from": from_agent,
        "to": to_agent,
        "subject": subject,
        "body": body,
        "status": "open",
    }
    try:
        async with make_async_client(DEFAULT_AGENT_BUS_URL, timeout=10.0) as client:
            await client.post("/turns", json=payload, headers=_bus_headers())
    except httpx.HTTPError as exc:
        logger.warning(
            "coord-thread notify failed: thread=%s subject=%s err=%s",
            coord_thread_id,
            subject,
            exc,
        )


async def post_coord_admit_pointer(
    *,
    coord_thread_id: str | None,
    worker_thread_id: str,
    to_agent: str,
    caller_agent: str | None,
    contract: str,
) -> None:
    if not coord_thread_id or coord_thread_id == worker_thread_id:
        return
    await _post_coord_turn(
        coord_thread_id=coord_thread_id,
        to_agent=to_agent,
        from_agent=caller_agent or "dispatch",
        subject=_ADMIT_SUBJECT,
        body=_admit_body(worker_thread_id=worker_thread_id, contract=contract),
    )
