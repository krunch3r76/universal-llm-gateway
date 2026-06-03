"""Handoff thread creation for POST /api/v1/team/handoff.

Separates the pointer-body construction and agent-bus thread creation from
route.py (thin layer) and service.py (generate-path orchestrator).
"""

from __future__ import annotations

import os
import re
from typing import Any

import httpx
from transport_utils import DEFAULT_AGENT_BUS_URL, make_async_client
from universal_logging import get_logger

from .admission import FrontierEndpointError

logger = get_logger(__name__)

_POINTER_MAX_LINES = 25

_POINTER_TEMPLATE = """\
{subject}

Read the packet:
  fs(sandbox="workspaces", op="read", path="{packet_path}")

The packet contains all six required blocks:
  <scope>, <invariants>, <task_guidance>, <mcp_capabilities>,
  <output_format>, <corpus>

Reply on this thread with findings. Use <need> only as last resort."""


def build_pointer_body(
    *,
    request_id: str,
    packet_path: str,
    subject: str,
    pointer_body: str | None,
) -> str:
    """Return the bus turn body.

    Uses caller override if given, else the standard handoff-dispatchers.mdc
    pointer template. Enforces ≤ _POINTER_MAX_LINES lines on the final body
    regardless of which path produced it.
    """
    body = (
        pointer_body
        if pointer_body is not None
        else _POINTER_TEMPLATE.format(subject=subject, packet_path=packet_path)
    )
    lines = body.splitlines()
    if len(lines) > _POINTER_MAX_LINES:
        raise FrontierEndpointError(
            request_id=request_id,
            field="pointer_body",
            reason=(
                "pointer body exceeds 25 lines; agent-bus is a table of "
                "contents, not a content carrier"
            ),
            status_code=422,
        )
    return body


def _slug_from_subject(subject: str) -> str:
    """Derive a kebab slug from a human subject string."""
    slug = subject.lower()
    slug = re.sub(r"[^a-z0-9]+", "-", slug)
    return slug[:50].strip("-") or "handoff"


async def create_handoff_thread(
    *,
    request_id: str,
    to_agent: str,
    subject: str,
    pointer_body: str,
    caller_agent: str | None,
    tags: list[str] | None,
) -> str:
    """POST to agent-bus /threads/with-turn; return thread_id.

    Token handling mirrors service.py lines 198–222: require AGENT_BUS_TOKEN
    (or ALLOW_UNSET_AGENT_BUS_TOKEN=true local bypass); if absent and no bypass,
    raise FrontierEndpointError(field="thread", status_code=503).
    Transport errors are translated to 502/503 FrontierEndpointError.
    """
    token = os.getenv("AGENT_BUS_TOKEN", "").strip()
    allow_unset = os.getenv("ALLOW_UNSET_AGENT_BUS_TOKEN", "").strip().lower() in (
        "1",
        "true",
        "yes",
    )
    if not token and not allow_unset:
        raise FrontierEndpointError(
            request_id=request_id,
            field="thread",
            reason=(
                "AGENT_BUS_TOKEN not configured; handoff requires agent-bus access. "
                "Set AGENT_BUS_TOKEN in the Stargate environment, or "
                "ALLOW_UNSET_AGENT_BUS_TOKEN=true for explicit local bypass."
            ),
            status_code=503,
        )

    slug = _slug_from_subject(subject)
    from_agent = caller_agent or "dispatch"
    effective_tags: list[str] = (
        tags
        if tags is not None
        else [
            f"agent:{to_agent}",
            "type:handoff",
        ]
    )

    payload: dict[str, Any] = {
        "slug": slug,
        "from": from_agent,
        "to": to_agent,
        "subject": subject,
        "body": pointer_body,
        "status": "open",
        "after_turn": 0,
        "tags": effective_tags,
    }

    headers = {"Authorization": f"Bearer {token}"} if token else {}
    try:
        async with make_async_client(DEFAULT_AGENT_BUS_URL, timeout=10.0) as client:
            resp = await client.post(
                "/threads/with-turn", headers=headers, json=payload
            )
    except httpx.HTTPError as exc:
        logger.error(
            "handoff agent-bus transport error: request_id=%s error=%s",
            request_id,
            exc,
        )
        raise FrontierEndpointError(
            request_id=request_id,
            field="thread",
            reason=f"Agent-bus unreachable: {exc}",
            status_code=503,
        ) from exc

    if resp.status_code >= 400:
        raise FrontierEndpointError(
            request_id=request_id,
            field="thread",
            reason=(
                f"Agent-bus thread creation failed: HTTP {resp.status_code} — "
                f"{resp.text[:200]}"
            ),
            status_code=502,
        )

    result: dict[str, Any] = resp.json()
    try:
        return str(result["thread"]["id"])
    except (KeyError, TypeError) as exc:
        raise FrontierEndpointError(
            request_id=request_id,
            field="thread",
            reason=f"Agent-bus 2xx response malformed: {exc}",
            status_code=502,
        ) from exc
