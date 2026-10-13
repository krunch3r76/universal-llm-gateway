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
from dataclasses import dataclass
from typing import Any, Literal

import httpx
from universal_logging import get_logger

from .env_predicates import SOURCE_GIW_LIVE, EnvironmentSnapshot, SourceRead

logger = get_logger(__name__)

_PROBE_TIMEOUT_S = 5.0


@dataclass(frozen=True, slots=True)
class GiwActiveWorkPayloadRead:
    """Raw GIW ``/api/v1/git/active-work`` probe — unknown ≠ none.

    ``status="error"`` means the probe could not observe; callers must not treat
    that as an empty drain. Truthy only when ``status=="ok"`` (including an
    empty-but-valid payload dict).
    """

    status: Literal["ok", "error"]
    payload: dict[str, Any] | None = None
    error_class: str | None = None

    def __bool__(self) -> bool:
        return self.status == "ok"

    def get(self, key: str, default: Any = None) -> Any:
        """Dict-like access for legacy callers (empty when unobservable)."""
        if self.payload is None:
            return default
        return self.payload.get(key, default)

    @property
    def as_dict(self) -> dict[str, Any] | None:
        """Payload dict when observable, else None (legacy interop)."""
        return self.payload if self.status == "ok" else None


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


async def fetch_giw_active_work_payload() -> GiwActiveWorkPayloadRead:
    """Return the raw GIW ``/api/v1/git/active-work`` JSON object when reachable.

    Probe failures return ``status="error"`` — never ``None`` coerced to "clear".
    """
    from transport_utils import make_async_client

    from ..restart_drain import GIT_INTEGRATION_WORKER_URL

    try:
        async with make_async_client(
            GIT_INTEGRATION_WORKER_URL, timeout=_PROBE_TIMEOUT_S
        ) as client:
            resp = await client.get("/api/v1/git/active-work")
            resp.raise_for_status()
            payload = resp.json()
    except Exception as exc:  # noqa: BLE001 — unobservable must not read as clear
        logger.warning("GIW active-work payload probe failed (%s)", exc)
        return GiwActiveWorkPayloadRead(
            status="error",
            error_class=type(exc).__name__,
        )
    if not isinstance(payload, dict):
        logger.warning("GIW active-work returned non-object payload")
        return GiwActiveWorkPayloadRead(
            status="error",
            error_class="MalformedPayload",
        )
    return GiwActiveWorkPayloadRead(status="ok", payload=payload)


def dispatch_ids_from_active_work(payload: dict[str, Any]) -> set[str]:
    """Collect active cursor-sdk dispatch ids from a GIW active-work payload."""
    ids: set[str] = set()
    cursor = payload.get("cursor_dispatches")
    if isinstance(cursor, dict):
        for raw in cursor.get("dispatch_ids") or []:
            if raw:
                ids.add(str(raw))
    lease = payload.get("write_lease")
    if isinstance(lease, dict) and lease.get("holder_dispatch_id"):
        ids.add(str(lease["holder_dispatch_id"]))
    for op in payload.get("active_ops") or []:
        if isinstance(op, dict) and op.get("op_id"):
            ids.add(str(op["op_id"]))
    return ids
