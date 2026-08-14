"""Wait until email-bridge UDS accepts pager traffic (not a systemd unit)."""

from __future__ import annotations

import asyncio
import os
import time
from pathlib import Path

from transport_utils import DEFAULT_EMAIL_BRIDGE_URL, EMAIL_BRIDGE_SOCK, make_async_client

_DEFAULT_TIMEOUT_S = 90.0
_DEFAULT_POLL_S = 1.0


def _timeout_s() -> float:
    raw = os.environ.get("PAGER_PEER_WAIT_TIMEOUT_S", "").strip()
    if not raw:
        return _DEFAULT_TIMEOUT_S
    try:
        return max(1.0, float(raw))
    except ValueError:
        return _DEFAULT_TIMEOUT_S


def _poll_s() -> float:
    raw = os.environ.get("PAGER_PEER_WAIT_POLL_S", "").strip()
    if not raw:
        return _DEFAULT_POLL_S
    try:
        return max(0.2, float(raw))
    except ValueError:
        return _DEFAULT_POLL_S


async def wait_for_pager_peer(
    *,
    timeout_s: float | None = None,
    poll_interval_s: float | None = None,
) -> tuple[bool, str]:
    """Block until email-bridge responds on UDS or timeout."""
    deadline = time.monotonic() + (timeout_s if timeout_s is not None else _timeout_s())
    poll = poll_interval_s if poll_interval_s is not None else _poll_s()
    sock_path = Path(os.environ.get("EMAIL_BRIDGE_SOCK", EMAIL_BRIDGE_SOCK))
    last_reason = "socket_absent"
    while time.monotonic() < deadline:
        if not sock_path.exists():
            last_reason = "socket_absent"
        else:
            try:
                async with make_async_client(
                    DEFAULT_EMAIL_BRIDGE_URL, timeout=5.0
                ) as client:
                    resp = await client.get("/health")
                    if resp.status_code < 500:
                        return True, "peer_ready"
                    last_reason = f"health_http_{resp.status_code}"
            except Exception as exc:
                last_reason = type(exc).__name__
        await asyncio.sleep(poll)
    return False, last_reason


def wait_for_pager_peer_sync(
    *,
    timeout_s: float | None = None,
    poll_interval_s: float | None = None,
) -> tuple[bool, str]:
    return asyncio.run(
        wait_for_pager_peer(timeout_s=timeout_s, poll_interval_s=poll_interval_s)
    )
