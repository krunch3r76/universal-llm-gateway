"""Live git-integration-worker hold probe for restart-shaped admission gating.

The restart-intent store alone is not sufficient to gate a restart-shaped
Next-pickup: an intent goes terminal at its drain deadline (``timeout`` is
alert-only and never kills), while the write-lease holder that caused the
deferral keeps running. Admitting on intent-terminal-only therefore reopens the
gate mid-hold and thrashes admit→orphan against the live holder.

This probe supplies the second, authoritative fact — is GIW holding live
mutating work *right now* — read from the same ``/api/v1/git/active-work``
payload the manage drain gate uses (``busy`` ∪ a held ``write_lease``).

Reachability posture (D-rule, §5.3 E2): ``ConnectError`` reports *not held*
(degraded CLEAR); timeout / HTTP / malformed payload is fail-closed HOLD.
"""

from __future__ import annotations

import time
from typing import Any

import httpx
from universal_logging import get_logger

from .env_predicates import SOURCE_GIW_LIVE, EnvironmentSnapshot, SourceRead

logger = get_logger(__name__)

_PROBE_TIMEOUT_S = 5.0


def _lease_is_held(payload: dict[str, Any]) -> bool:
    lease = payload.get("write_lease")
    if not isinstance(lease, dict):
        return False
    return bool(lease.get("holder_dispatch_id"))


def _active_work_held(payload: dict[str, Any]) -> bool:
    return bool(payload.get("busy")) or _lease_is_held(payload)


async def read_giw_active_work() -> SourceRead:
    """Fetch GIW ``/api/v1/git/active-work`` as a typed ``SourceRead`` (E2 source)."""
    from transport_utils import make_async_client

    from ..restart_drain import GIT_INTEGRATION_WORKER_URL

    started = time.monotonic()
    try:
        async with make_async_client(
            GIT_INTEGRATION_WORKER_URL, timeout=_PROBE_TIMEOUT_S
        ) as client:
            resp = await client.get("/api/v1/git/active-work")
            resp.raise_for_status()
            payload = resp.json()
    except httpx.ConnectError:
        latency_ms = (time.monotonic() - started) * 1000.0
        return SourceRead(
            status="degraded",
            payload=False,
            error_class="ConnectError",
            latency_ms=latency_ms,
            scope="tick",
        )
    except Exception as exc:  # noqa: BLE001 — unknown state gates fail-closed
        latency_ms = (time.monotonic() - started) * 1000.0
        logger.warning("GIW live-hold probe failed (%s); treating as held", exc)
        return SourceRead(
            status="error",
            payload=True,
            error_class=type(exc).__name__,
            latency_ms=latency_ms,
            scope="tick",
        )
    latency_ms = (time.monotonic() - started) * 1000.0
    if not isinstance(payload, dict):
        logger.warning("GIW active-work returned non-object; treating as held")
        return SourceRead(
            status="error",
            payload=True,
            error_class="MalformedPayload",
            latency_ms=latency_ms,
            scope="tick",
        )
    return SourceRead(
        status="ok",
        payload=_active_work_held(payload),
        latency_ms=latency_ms,
        scope="tick",
    )


async def build_tick_env_snapshot() -> EnvironmentSnapshot:
    """Resolve tick-scoped ENV sources into one snapshot (§5.1 P1)."""
    from datetime import UTC, datetime

    from ..restart_intent_store import RestartIntentStore
    from .env_predicates import DEFAULT_SNAPSHOT_TTL_S, SOURCE_GIW_DRAIN

    giw_live = await read_giw_active_work()
    intent = RestartIntentStore.instance().active_for_service("git_integration_worker")
    return EnvironmentSnapshot(
        observed_at=datetime.now(UTC),
        ttl_s=DEFAULT_SNAPSHOT_TTL_S,
        sources={
            SOURCE_GIW_LIVE: giw_live,
            SOURCE_GIW_DRAIN: SourceRead(status="ok", payload=intent, scope="tick"),
        },
    )


async def probe_giw_live_hold() -> bool:
    """Return True when GIW holds live mutating work (busy ∪ write lease)."""
    read = await read_giw_active_work()
    if read.status == "degraded" and read.error_class == "ConnectError":
        return False
    if read.status == "ok":
        return bool(read.payload)
    return True
