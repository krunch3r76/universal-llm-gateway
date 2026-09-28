"""One liveness predicate for git-worker drain recovery.

Dead iff the drain-state snapshot has been absent for the confirm window
and the health/pid probe does not see a process. A single missed probe, a
snapshot that arrived, or a health checker that still sees a process is
not dead — those cases must not start a worker.
"""

from __future__ import annotations

from typing import Any

from services.git_integration_worker.drain_progress import HEARTBEAT_TTL_S

# Same window the await loop already required (~45 polls at 2s). A ceiling
# re-probe uses this, not a single None.
PROBE_UNREACHABLE_WINDOW_S = HEARTBEAT_TTL_S
RECONCILE_INTERVAL_S = 2.0


def unreachable_window_s() -> float:
    """Confirm window. Tests patch ``PROBE_UNREACHABLE_WINDOW_S``."""
    return PROBE_UNREACHABLE_WINDOW_S


def reconcile_interval_s() -> float:
    """Default poll interval. The supervisor may pass its own interval."""
    return RECONCILE_INTERVAL_S


def polls_for_window(window_s: float, interval_s: float) -> int:
    """Minimum consecutive reconcile polls to cover ``window_s``."""
    return max(1, int(window_s / max(interval_s, 0.001)))


def health_status_means_absent(status: object) -> bool:
    """True only for an explicit stopped status. Unknown is not absent."""
    value = getattr(status, "value", status)
    return value == "stopped"


def drain_target_is_dead(
    *,
    consecutive_snapshot_misses: int,
    miss_threshold: int,
    health_pid_absent: bool,
) -> bool:
    """Dead iff no snapshot for the window and the health/pid probe is absent."""
    return consecutive_snapshot_misses >= miss_threshold and health_pid_absent


async def probe_drain_snapshot(url: str) -> dict[str, Any] | None:
    """One drain-state read. Any failure is a miss, not a snapshot."""
    from transport_utils import make_async_client

    try:
        async with make_async_client(url, timeout=2.0) as client:
            resp = await client.get("/api/v1/git/admin/drain-state")
            resp.raise_for_status()
            body = resp.json()
    except Exception:
        return None
    return body if isinstance(body, dict) else None
