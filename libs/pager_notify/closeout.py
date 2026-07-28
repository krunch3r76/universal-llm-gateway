"""CLOSEOUT pager — prefer thread so-what title over DIRECTIVE subject."""

from __future__ import annotations

import logging
import os
from typing import Any

from transport_utils import DEFAULT_AGENT_BUS_URL, make_async_client

from pager_notify.client import notify_pager
from pager_notify.so_what import (
    compose_done_summary,
    format_closeout_pager,
    resolve_so_what_summary,
)

logger = logging.getLogger(__name__)

_TIMEOUT_S = 10.0


def _auth_headers() -> dict[str, str]:
    token = os.environ.get("AGENT_BUS_TOKEN", "").strip()
    return {"Authorization": f"Bearer {token}"} if token else {}


async def _fetch_summary(thread_id: str) -> str:
    try:
        async with make_async_client(
            DEFAULT_AGENT_BUS_URL, timeout=_TIMEOUT_S
        ) as client:
            resp = await client.get(
                f"/threads/{thread_id}",
                headers=_auth_headers(),
            )
            resp.raise_for_status()
            detail: dict[str, Any] = dict(resp.json())
    except Exception:
        logger.debug("closeout pager: fetch summary failed thread=%s", thread_id)
        return ""
    raw = str(detail.get("summary") or "")
    if not raw and isinstance(detail.get("thread"), dict):
        raw = str((detail.get("thread") or {}).get("summary") or "")
    return raw.strip()


async def _patch_summary(thread_id: str, summary: str) -> None:
    try:
        async with make_async_client(
            DEFAULT_AGENT_BUS_URL, timeout=_TIMEOUT_S
        ) as client:
            resp = await client.patch(
                f"/threads/{thread_id}",
                json={"summary": summary},
                headers=_auth_headers(),
            )
            resp.raise_for_status()
    except Exception:
        logger.debug("closeout pager: patch summary failed thread=%s", thread_id)


async def notify_closeout_complete(
    *,
    thread_id: str,
    status: str,
    dispatch_id: str = "",
    closeout_body: str = "",
    sdk_body: str = "",
    mark_done: bool = False,
) -> bool:
    """Refresh so-what when present; page with outcome-led subject/body."""
    prior = await _fetch_summary(thread_id)
    from_body = resolve_so_what_summary(None, closeout_body) or resolve_so_what_summary(
        None, sdk_body
    )
    achieved = from_body or prior
    if mark_done and achieved:
        achieved = compose_done_summary(achieved, reason=status)
    if achieved and achieved != prior:
        await _patch_summary(thread_id, achieved)
    subject, body = format_closeout_pager(
        status=status,
        thread_id=thread_id,
        summary=achieved or prior,
        dispatch_id=dispatch_id,
    )
    return await notify_pager(subject, body, tag="closeout")


__all__ = ["notify_closeout_complete"]
