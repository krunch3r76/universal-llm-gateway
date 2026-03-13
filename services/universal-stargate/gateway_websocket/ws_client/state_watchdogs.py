"""TTL watchdog functions for self-healing stale WebSocket state."""

import time as _time

from universal_logging import get_logger

logger = get_logger(__name__)


def expire_stale_busy_models(
    busy_models: set[str],
    busy_since: dict[str, float],
    ttl_seconds: float,
) -> None:
    """Auto-clear busy models that exceeded TTL without refresh."""
    now = _time.monotonic()
    stale = [m for m in busy_models if now - busy_since.get(m, 0) > ttl_seconds]
    for model_id in stale:
        busy_models.discard(model_id)
        _ = busy_since.pop(model_id, None)
        logger.warning(
            "Auto-cleared stale busy state for %s (TTL %.0fs)",
            model_id,
            ttl_seconds,
        )


def expire_stale_loading_models(
    loading_models: set[str],
    loading_since: dict[str, float],
    ttl_seconds: float,
) -> None:
    """Auto-clear loading models that exceeded TTL without completion."""
    now = _time.monotonic()
    stale = [m for m in loading_models if now - loading_since.get(m, 0) > ttl_seconds]
    for model_id in stale:
        elapsed = now - loading_since.get(model_id, 0)
        loading_models.discard(model_id)
        _ = loading_since.pop(model_id, None)
        logger.warning(
            "Auto-cleared stuck loading state for %s (TTL %.0fs, elapsed %.0fs)",
            model_id,
            ttl_seconds,
            elapsed,
        )
