"""Async client for email-bridge pager notify (fail-open)."""

from __future__ import annotations

import logging
import os

from transport_utils import DEFAULT_EMAIL_BRIDGE_URL, make_async_client

logger = logging.getLogger(__name__)

# SMTP relay in email-bridge blocks until send completes (~15s observed); 15s
# client timeout caused intermittent httpx.ReadTimeout → life notify status:failed.
_TIMEOUT_S = 45.0


def pager_enabled() -> bool:
    raw = os.environ.get("PAGER_NOTIFY_ENABLED", "1").strip().lower()
    return raw not in {"0", "false", "no", "off"}


async def notify_pager(
    subject: str,
    body: str,
    *,
    tag: str = "",
) -> bool:
    """Fire Fi SMS pager. Returns True when email-bridge reports sent."""
    if not pager_enabled():
        return False
    payload = {
        "subject": (subject or "ULG")[:120],
        "body": (body or "")[:300],
        "tag": (tag or "")[:40],
    }
    try:
        async with make_async_client(
            DEFAULT_EMAIL_BRIDGE_URL, timeout=_TIMEOUT_S
        ) as client:
            resp = await client.post("/pager/notify", json=payload)
            if resp.status_code >= 400:
                logger.warning(
                    "pager notify HTTP %s tag=%s body=%s",
                    resp.status_code,
                    tag,
                    resp.text[:200],
                )
                return False
            data = resp.json()
            return str(data.get("status")) == "sent"
    except Exception:
        logger.exception("pager notify failed tag=%s", tag)
        return False
