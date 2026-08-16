"""Raw agent-bus HTTP transport for delivery.

Three async helpers wrap the three agent-bus endpoints the delivery
package touches:

- ``_post_turn`` — POST /turns (used by both legacy and on-behalf paths)
- ``_close_thread`` — PATCH /threads/{id}/close (legacy ephemeral lifecycle)
- ``_fetch_thread_last_turn_from`` — GET /threads/{id} (on-behalf to_agent fallback)

All three NEVER raise — network and parse errors are caught and surfaced as
a synthetic 599 status code (POST/PATCH) or ``None`` return (GET) so caller
emit logic stays straight-line without try/except scaffolding.

This module is the sole import site for ``transport_utils.make_async_client``
in the package. Tests patch this submodule's ``make_async_client`` name
(``systems.pipeline.core.execution.async_tracker_delivery.agent_bus_http.make_async_client``)
to inject mock transports.
"""

from __future__ import annotations

from typing import Any

import httpx
from transport_utils import make_async_client
from universal_logging import get_logger

from .constants import _HTTP_TIMEOUT_S

logger = get_logger(__name__)


async def _close_thread(
    *,
    url: str,
    auth_token: str,
    thread: str,
    summary: str,
) -> tuple[int, str]:
    """PATCH /threads/{id}/close; return ``(status_code, response_text)``.

    Never raises — wraps network errors into 599 so caller's emit stays
    straight-line.
    """
    payload: dict[str, Any] = {"summary": summary, "mark_all_read": True}
    try:
        async with make_async_client(url, timeout=_HTTP_TIMEOUT_S) as client:
            response = await client.patch(
                f"/threads/{thread}/close",
                headers={"Authorization": f"Bearer {auth_token}"},
                json=payload,
            )
            return response.status_code, response.text
    except httpx.HTTPError as exc:
        logger.error("Agent-bus ephemeral close transport error: %s", exc)
        return 599, f"transport_error: {exc}"


async def _post_turn(
    *,
    url: str,
    auth_token: str,
    thread: str,
    from_agent: str,
    to_agent: str,
    subject: str,
    body: str,
    attachments: list[dict[str, Any]] | None = None,
    allow_long_body: bool = False,
) -> tuple[int, str]:
    """POST /turns; return ``(status_code, response_text)``.

    Never raises — wraps network errors into a 599 synthetic status so
    the caller's emit logic stays straight-line.
    """
    payload: dict[str, Any] = {
        "thread": thread,
        "from": from_agent,
        "to": to_agent,
        "subject": subject,
        "body": body,
    }
    if allow_long_body:
        payload["allow_long_body"] = True
    if attachments:
        payload["attachments"] = attachments
    try:
        async with make_async_client(url, timeout=_HTTP_TIMEOUT_S) as client:
            response = await client.post(
                "/turns",
                headers={"Authorization": f"Bearer {auth_token}"},
                json=payload,
            )
            return response.status_code, response.text
    except httpx.HTTPError as exc:
        logger.error("Agent-bus delivery transport error: %s", exc)
        return 599, f"transport_error: {exc}"


async def _fetch_thread_close_context(
    thread: str, *, url: str, auth_token: str
) -> tuple[str | None, list[str]]:
    """Return ``(summary, tags)`` for ephemeral close composition."""
    headers = {"Authorization": f"Bearer {auth_token}"} if auth_token else {}
    try:
        async with make_async_client(url, timeout=5.0) as client:
            resp = await client.get(f"/threads/{thread}", headers=headers)
        if resp.status_code >= 400:
            return None, []
        data = resp.json()
        summary = data.get("summary")
        tags = list(data.get("tags") or [])
        return (str(summary) if summary else None), tags
    except (httpx.HTTPError, ValueError) as exc:
        logger.warning(
            "Thread close-context fetch failed: thread=%s error=%s", thread, exc
        )
        return None, []


async def _fetch_thread_last_turn_from(
    thread: str, *, url: str, auth_token: str
) -> str | None:
    """Return the agent who posted the most recent turn; ``None`` on error/empty.

    Used by the on-behalf delivery path as a ``to_agent`` fallback when
    ``record.caller_agent`` is unset.
    """
    headers = {"Authorization": f"Bearer {auth_token}"} if auth_token else {}
    try:
        async with make_async_client(url, timeout=5.0) as client:
            resp = await client.get(f"/threads/{thread}", headers=headers)
        if resp.status_code >= 400:
            return None
        data = resp.json()
        last_from = data.get("last_turn_from")
        return str(last_from) if last_from else None
    except (httpx.HTTPError, ValueError) as exc:
        logger.warning(
            "Thread last-turn-from fetch failed: thread=%s error=%s", thread, exc
        )
        return None
