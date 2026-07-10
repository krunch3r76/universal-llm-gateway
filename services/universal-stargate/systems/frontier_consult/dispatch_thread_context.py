"""Resolve team-dispatch prompt context from the caller-owned agent-bus thread."""

from __future__ import annotations

import os
import re
from typing import Any
from urllib.parse import urlencode

import httpx
from transport_utils import DEFAULT_AGENT_BUS_URL, make_async_client

from .admission import FrontierEndpointError

_PLACEHOLDER_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"<\s*INSERT[^>\n]*>", re.IGNORECASE),
    re.compile(r"<[^>\n]*HERE\s*>", re.IGNORECASE),
    re.compile(r"\{\{[^{}\n]+\}\}"),
)

# First-line markers of server-posted turns that must never become a model
# prompt. Single-thread Q/R reuse posts the generate pointer (and, on failure,
# a failure turn) onto the dispatch thread itself; a subsequent dispatch
# against the same thread would otherwise consume that server turn as the
# prompt verbatim (friction 23301 — threads 4741/4744 cascading pointer
# prompts). Markers mirror ``build_generate_dispatch_pointer``, the
# cursor-sdk packet pointer, and ``_post_api_role_dispatch_failure_turn``.
_SERVER_TURN_MARKERS: tuple[str, ...] = (
    "generate dispatch — prompt on dispatch thread",
    "dispatch — see packet `",
    "generate dispatch failed (",
)


def is_server_dispatch_turn_body(text: str) -> bool:
    """True when *text* opens with a server-posted pointer/failure envelope."""
    stripped = text.strip()
    first_line = stripped.splitlines()[0] if stripped else ""
    return any(marker in first_line for marker in _SERVER_TURN_MARKERS)


def _auth_headers() -> dict[str, str]:
    token = os.getenv("AGENT_BUS_TOKEN", "").strip()
    return {"Authorization": f"Bearer {token}"} if token else {}


def reject_unresolved_placeholders(
    *, request_id: str, text: str, field: str = "dispatch_thread_id"
) -> None:
    """Reject template-marker residue before a dispatched model can run."""
    for pattern in _PLACEHOLDER_PATTERNS:
        match = pattern.search(text)
        if match is not None:
            raise FrontierEndpointError(
                request_id=request_id,
                field=field,
                reason=(
                    "dispatch thread body contains unresolved template marker "
                    f"{match.group(0)!r}; replace placeholders before dispatch"
                ),
                status_code=422,
                code="dispatch_context_placeholder",
            )


async def read_latest_dispatch_thread_body(
    *, request_id: str, dispatch_thread_id: str
) -> str:
    """Read the latest turn body from the caller-owned dispatch thread."""
    thread = dispatch_thread_id.strip()
    if not thread:
        raise FrontierEndpointError(
            request_id=request_id,
            field="dispatch_thread_id",
            reason="dispatch_thread_id is required",
            status_code=422,
        )

    qs = urlencode({"thread": thread, "last": 1})
    try:
        async with make_async_client(DEFAULT_AGENT_BUS_URL, timeout=10.0) as client:
            resp = await client.get(f"/turns?{qs}", headers=_auth_headers())
    except httpx.HTTPError as exc:
        raise FrontierEndpointError(
            request_id=request_id,
            field="dispatch_thread_id",
            reason=f"could not read dispatch thread {thread!r}: {exc}",
            status_code=503,
            code="dispatch_thread_read_failed",
        ) from exc

    if resp.status_code == 404:
        raise FrontierEndpointError(
            request_id=request_id,
            field="dispatch_thread_id",
            reason=f"dispatch thread {thread!r} was not found",
            status_code=422,
            code="dispatch_thread_not_found",
        )
    if resp.status_code >= 400:
        raise FrontierEndpointError(
            request_id=request_id,
            field="dispatch_thread_id",
            reason=(
                f"agent-bus returned {resp.status_code} while reading dispatch "
                f"thread {thread!r}: {resp.text[:200]}"
            ),
            status_code=503,
            code="dispatch_thread_read_failed",
        )

    payload: dict[str, Any] = resp.json()
    turns = payload.get("turns")
    if not isinstance(turns, list) or not turns:
        raise FrontierEndpointError(
            request_id=request_id,
            field="dispatch_thread_id",
            reason=f"dispatch thread {thread!r} has no turns to dispatch",
            status_code=422,
            code="dispatch_thread_empty",
        )
    body = turns[-1].get("body") if isinstance(turns[-1], dict) else None
    if not isinstance(body, str) or not body.strip():
        raise FrontierEndpointError(
            request_id=request_id,
            field="dispatch_thread_id",
            reason=f"latest turn on dispatch thread {thread!r} has an empty body",
            status_code=422,
            code="dispatch_thread_empty_body",
        )
    text = body.strip()
    if is_server_dispatch_turn_body(text):
        raise FrontierEndpointError(
            request_id=request_id,
            field="dispatch_thread_id",
            reason=(
                f"latest turn on dispatch thread {thread!r} is a server-posted "
                "dispatch pointer/failure envelope, not a caller prompt. Post a "
                "fresh prompt turn on the thread before dispatching again, or "
                "pass split_thread=true to keep dispatch turns off the prompt "
                "thread"
            ),
            status_code=422,
            code="dispatch_thread_latest_is_pointer",
        )
    reject_unresolved_placeholders(request_id=request_id, text=text)
    return text


def as_user_message(text: str) -> list[dict[str, str]]:
    """Internal pipeline request shape for a resolved dispatch-thread prompt."""
    return [{"role": "user", "content": text}]
