"""Async client for email-bridge pager notify (fail-open)."""

from __future__ import annotations

import logging
import os
import sys
from dataclasses import dataclass
from typing import Literal

from transport_utils import DEFAULT_EMAIL_BRIDGE_URL, make_async_client

from pager_notify.so_what import SMS_BODY_MAX, SMS_SUBJECT_MAX

logger = logging.getLogger(__name__)

# SMTP relay in email-bridge blocks until send completes (~15s observed); 15s
# client timeout caused intermittent httpx.ReadTimeout → life notify status:failed.
_TIMEOUT_S = 45.0

NotifyStatus = Literal["sent", "failed"]


@dataclass(frozen=True, slots=True)
class NotifyResult:
    """Pager delivery outcome — truthy only when ``status == \"sent\"``."""

    status: NotifyStatus
    reason: str = ""
    error: str = ""

    def __bool__(self) -> bool:
        return self.status == "sent"

    @classmethod
    def sent(cls) -> NotifyResult:
        return cls(status="sent")

    @classmethod
    def failed(cls, reason: str, *, error: str = "") -> NotifyResult:
        return cls(status="failed", reason=reason, error=error)


def _pytest_detected() -> bool:
    if os.environ.get("PYTEST_CURRENT_TEST"):
        return True
    return "pytest" in sys.modules


def _pager_disabled_reason() -> str | None:
    """Return a stable failure reason when pager is off, else ``None``."""
    raw = os.environ.get("PAGER_NOTIFY_ENABLED")
    if raw is not None:
        if raw.strip().lower() in {"0", "false", "no", "off"}:
            return "PAGER_NOTIFY_ENABLED=0"
        return None
    if _pytest_detected():
        return "pytest"
    return None


def pager_enabled() -> bool:
    """Pager on/off — explicit env beats inference; fail-closed under pytest."""
    return _pager_disabled_reason() is None


async def notify_pager(
    subject: str,
    body: str,
    *,
    tag: str = "",
    wait_for_peer: bool = True,
) -> NotifyResult:
    """Fire Fi SMS pager. Returns sent/failed with machine-readable reason."""
    disabled_reason = _pager_disabled_reason()
    if disabled_reason is not None:
        return NotifyResult.failed(disabled_reason)
    if wait_for_peer:
        from pager_notify.peer_wait import wait_for_pager_peer

        ready, reason = await wait_for_pager_peer()
        if not ready:
            return NotifyResult.failed("peer_not_ready", error=reason)
    payload = {
        "subject": (subject or "ULG")[:SMS_SUBJECT_MAX],
        "body": (body or "")[:SMS_BODY_MAX],
        "tag": (tag or "")[:40],
    }
    try:
        async with make_async_client(
            DEFAULT_EMAIL_BRIDGE_URL, timeout=_TIMEOUT_S
        ) as client:
            resp = await client.post("/pager/notify", json=payload)
            if resp.status_code >= 400:
                snippet = (resp.text or "")[:200]
                logger.warning(
                    "pager notify HTTP %s tag=%s body=%s",
                    resp.status_code,
                    tag,
                    snippet,
                )
                return NotifyResult.failed(
                    f"HTTP {resp.status_code}",
                    error=snippet,
                )
            data = resp.json()
            bridge_status = str(data.get("status") or "")
            if bridge_status != "sent":
                detail = str(
                    data.get("error") or data.get("message") or data.get("reason") or ""
                )[:200]
                logger.warning(
                    "pager notify bridge status=%s tag=%s detail=%s",
                    bridge_status or "unknown",
                    tag,
                    detail,
                )
                return NotifyResult.failed(
                    f"bridge status: {bridge_status or 'unknown'}",
                    error=detail,
                )
            return NotifyResult.sent()
    except Exception as exc:
        logger.exception("pager notify failed tag=%s", tag)
        return NotifyResult.failed(type(exc).__name__, error=str(exc)[:200])
