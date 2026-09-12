"""Best-effort CDP event publish helpers (extracted from ``cdp_events``).

Swallowed publishes are rate-limited to one log line per (signal, reason) so a
dead Event Service bus stays legible without flooding the lane.
"""

from __future__ import annotations

from typing import Any

from universal_event_bus import Event
from universal_logging import get_logger

logger = get_logger(__name__)

# (signal, reason) pairs already reported; keeps a dead publisher to one line.
_SWALLOWED_SEEN: set[tuple[str, str]] = set()


def publish_cdp_event(event: Event) -> bool:
    """Best-effort publish via Stargate proxy event bus.

    Returns True when ``publish_from_sync`` ran. False when the proxy has no
    bus or publish raised. Horizon sizing retries on False; other callers
    ignore the return (observability must not fail the lane).
    """
    payload = event.payload or {}
    request_id = payload.get("request_id")
    execution_id = payload.get("execution_id")

    def _debug(outcome: str, *, exc_type: str | None = None) -> None:
        parts = [
            f"cdp.event.publish outcome={outcome}",
            f"signal={event.signal}",
        ]
        if request_id is not None:
            parts.append(f"request_id={request_id}")
        if execution_id is not None:
            parts.append(f"execution_id={execution_id}")
        if exc_type is not None:
            parts.append(f"exc_type={exc_type}")
        logger.debug(" ".join(parts))

    try:
        from systems.proxy.dependencies import get_proxy

        proxy = get_proxy()
        event_bus = getattr(proxy, "event_bus", None)
        if event_bus is None:
            _debug("bus_none")
            _warn_swallowed(event.signal, "proxy has no event_bus")
            return False
        event_bus.publish_from_sync(event)
        _debug("ok")
        return True
    except RuntimeError as exc:
        if "Proxy not initialized" in str(exc):
            _debug("proxy_uninitialized")
        else:
            _debug("publish_exception", exc_type=type(exc).__name__)
        _warn_swallowed(event.signal, f"{type(exc).__name__}: {exc}")
        return False
    except Exception as exc:  # noqa: BLE001 — observability must not fail the lane
        _debug("publish_exception", exc_type=type(exc).__name__)
        _warn_swallowed(event.signal, f"{type(exc).__name__}: {exc}")
        return False


def publish_cdp_kwargs(factory: Any, **kwargs: Any) -> bool:
    """Build + publish a CDP event factory (swallow publish errors).

    Returns False only on a failed delivery (bus missing, publish raised, or
    factory raised). None-returning test stubs count as delivered.
    """
    kwarg_names = ",".join(sorted(kwargs))
    try:
        event = factory(**kwargs)
        delivered = publish_cdp_event(event)
        if delivered is False:
            return False
        return True
    except Exception as exc:  # noqa: BLE001
        logger.debug(
            "cdp.event.publish outcome=factory_exception "
            f"signal={getattr(factory, '__name__', 'cdp.generate.?')} "
            f"exc_type={type(exc).__name__} kwarg_names={kwarg_names}"
        )
        _warn_swallowed(
            getattr(factory, "__name__", "cdp.generate.?"),
            f"{type(exc).__name__}: {exc}",
        )
        return False


def _warn_swallowed(signal: str, reason: str) -> None:
    """Log a dropped CDP event once per (signal, reason) for this process.

    A silent swallow here is what made the 2026-07-30/31 emission blackout
    unattributable: 67 legs produced no ``cdp.generate.*`` event and no trace
    of why. Rate-limited to one line per distinct cause so a persistently dead
    publisher stays legible instead of flooding.
    """
    key = (signal, reason)
    if key in _SWALLOWED_SEEN:
        return
    _SWALLOWED_SEEN.add(key)
    logger.warning(
        "cdp event dropped (first occurrence this process): signal=%s reason=%s",
        signal,
        reason,
    )
