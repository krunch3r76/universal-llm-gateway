"""Live git-integration-worker hold probe for restart-shaped admission gating.

The restart-intent store alone is not sufficient to gate a restart-shaped
Next-pickup: an intent goes terminal at its drain deadline (``timeout`` is
alert-only and never kills), while the write-lease holder that caused the
deferral keeps running. Admitting on intent-terminal-only therefore reopens the
gate mid-hold and thrashes admit→orphan against the live holder.

This probe supplies the second, authoritative fact — is GIW holding live
mutating work *right now* — read from the same ``/api/v1/git/active-work``
payload the manage drain gate uses (``busy`` ∪ a held ``write_lease``).

Reachability posture: a worker that refuses the connection cannot be holding a
lease, so ``ConnectError`` reports *not held* (otherwise a stopped worker would
permanently starve restart pickups). Any other failure — timeout, malformed
payload, HTTP error — is fail-closed to *held*, because an admission racing an
unknown holder is the expensive outcome.
"""

from __future__ import annotations

from typing import Any

import httpx
from universal_logging import get_logger

logger = get_logger(__name__)

_PROBE_TIMEOUT_S = 5.0


def _lease_is_held(payload: dict[str, Any]) -> bool:
    lease = payload.get("write_lease")
    if not isinstance(lease, dict):
        return False
    return bool(lease.get("holder_dispatch_id"))


async def probe_giw_live_hold() -> bool:
    """Return True when GIW holds live mutating work (busy ∪ write lease)."""
    from transport_utils import make_async_client

    from ..restart_drain import GIT_INTEGRATION_WORKER_URL

    try:
        async with make_async_client(
            GIT_INTEGRATION_WORKER_URL, timeout=_PROBE_TIMEOUT_S
        ) as client:
            resp = await client.get("/api/v1/git/active-work")
            resp.raise_for_status()
            payload = resp.json()
    except httpx.ConnectError:
        return False
    except Exception as exc:  # noqa: BLE001 — unknown state gates fail-closed
        logger.warning("GIW live-hold probe failed (%s); treating as held", exc)
        return True
    if not isinstance(payload, dict):
        logger.warning("GIW active-work returned non-object; treating as held")
        return True
    return bool(payload.get("busy")) or _lease_is_held(payload)
