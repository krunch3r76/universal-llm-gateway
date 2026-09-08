"""Charter tick leg — periodic continuity consolidation sweep.

Runs after each floor/wake batch (same postamble slot as friction reconcile).
Failures are logged and never abort the tick.
"""

from __future__ import annotations

from typing import Any

from universal_logging import get_logger

logger = get_logger(__name__)


async def sweep_role_roots_on_tick() -> list[dict[str, Any]]:
    """Scan ``role:root`` houses for stale WATERMARKs; enqueue missed folds."""
    from agent_bus_store.continuity_sweep import run_continuity_sweep

    try:
        return run_continuity_sweep()
    except Exception:  # noqa: BLE001 — tick must continue
        logger.exception("charter-runner continuity sweep failed")
        return []


__all__ = ["sweep_role_roots_on_tick"]
