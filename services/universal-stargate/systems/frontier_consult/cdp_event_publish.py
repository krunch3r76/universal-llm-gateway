"""Best-effort CDP event publish helpers (extracted from ``cdp_events``).

Swallowed publishes are rate-limited to one log line per (signal, reason) so a
dead Event Service bus stays legible without flooding the lane.
"""

from __future__ import annotations

import asyncio
import os
import threading
from typing import Any

from universal_event_bus import Event
from universal_logging import get_logger

logger = get_logger(__name__)

# (signal, reason) pairs already reported; keeps a dead publisher to one line.
_SWALLOWED_SEEN: set[tuple[str, str]] = set()


def _resolve_get_proxy() -> Any:
    """Lazy proxy accessor — patchable in unit tests without ``systems.proxy``."""
    from systems.proxy.dependencies import get_proxy

    return get_proxy()


def _publish_context_fields() -> dict[str, Any]:
    try:
        asyncio.get_running_loop()
        has_running_loop = True
    except RuntimeError:
        has_running_loop = False
    return {
        "pid": os.getpid(),
        "thread": threading.get_ident(),
        "has_running_loop": has_running_loop,
    }


def _log_publish_outcome(
    event: Event,
    outcome: str,
    *,
    request_id: Any,
    execution_id: Any,
    exc_type: str | None = None,
    kwarg_names: str | None = None,
) -> None:
    ctx = _publish_context_fields()
    signal = event.signal
    parts = [
        f"cdp.event.publish outcome={outcome}",
        f"signal={signal}",
    ]
    if request_id is not None:
        parts.append(f"request_id={request_id}")
    if execution_id is not None:
        parts.append(f"execution_id={execution_id}")
    if exc_type is not None:
        parts.append(f"exc_type={exc_type}")
    if kwarg_names is not None:
        parts.append(f"kwarg_names={kwarg_names}")
    parts.extend(
        [
            f"pid={ctx['pid']}",
            f"thread={ctx['thread']}",
            f"has_running_loop={ctx['has_running_loop']}",
        ]
    )
    message = " ".join(parts)
    if outcome == "ok":
        logger.debug(message)
    elif outcome in ("proxy_uninitialized", "bus_none"):
        logger.warning(message)
    else:
        logger.error(message)


def publish_cdp_event(event: Event) -> bool:
    """Best-effort publish via Stargate proxy event bus.

    Returns True when ``publish_from_sync`` ran. False when the proxy has no
    bus or publish raised. Horizon sizing retries on False; other callers
    ignore the return (observability must not fail the lane).
    """
    payload = event.payload or {}
    request_id = payload.get("request_id")
    execution_id = payload.get("execution_id")

    try:
        proxy = _resolve_get_proxy()
        event_bus = getattr(proxy, "event_bus", None)
        if event_bus is None:
            _log_publish_outcome(
                event,
                "bus_none",
                request_id=request_id,
                execution_id=execution_id,
            )
            _warn_swallowed(event.signal, "proxy has no event_bus")
            return False
        event_bus.publish_from_sync(event)
        _log_publish_outcome(
            event,
            "ok",
            request_id=request_id,
            execution_id=execution_id,
        )
        return True
    except RuntimeError as exc:
        if "Proxy not initialized" in str(exc):
            _log_publish_outcome(
                event,
                "proxy_uninitialized",
                request_id=request_id,
                execution_id=execution_id,
            )
        else:
            _log_publish_outcome(
                event,
                "publish_exception",
                request_id=request_id,
                execution_id=execution_id,
                exc_type=type(exc).__name__,
            )
        _warn_swallowed(event.signal, f"{type(exc).__name__}: {exc}")
        return False
    except Exception as exc:  # noqa: BLE001 — observability must not fail the lane
        _log_publish_outcome(
            event,
            "publish_exception",
            request_id=request_id,
            execution_id=execution_id,
            exc_type=type(exc).__name__,
        )
        _warn_swallowed(event.signal, f"{type(exc).__name__}: {exc}")
        return False


def publish_cdp_kwargs(factory: Any, **kwargs: Any) -> bool:
    """Build + publish a CDP event factory (swallow publish errors).

    Returns False only on a failed delivery (bus missing, publish raised, or
    factory raised). None-returning test stubs count as delivered.
    """
    kwarg_names = ",".join(sorted(kwargs))
    signal_name = getattr(factory, "__name__", "cdp.generate.?")
    try:
        event = factory(**kwargs)
    except Exception as exc:  # noqa: BLE001
        _log_factory_exception(
            signal_name=signal_name,
            request_id=kwargs.get("request_id"),
            execution_id=kwargs.get("execution_id"),
            exc_type=type(exc).__name__,
            kwarg_names=kwarg_names,
        )
        _warn_swallowed(signal_name, f"{type(exc).__name__}: {exc}")
        return False

    delivered = publish_cdp_event(event)
    if delivered is False:
        return False
    return True


def _log_factory_exception(
    *,
    signal_name: str,
    request_id: Any,
    execution_id: Any,
    exc_type: str,
    kwarg_names: str,
) -> None:
    ctx = _publish_context_fields()
    message = " ".join(
        [
            "cdp.event.publish outcome=factory_exception",
            f"signal={signal_name}",
            *( [f"request_id={request_id}"] if request_id is not None else [] ),
            *( [f"execution_id={execution_id}"] if execution_id is not None else [] ),
            f"exc_type={exc_type}",
            f"kwarg_names={kwarg_names}",
            f"pid={ctx['pid']}",
            f"thread={ctx['thread']}",
            f"has_running_loop={ctx['has_running_loop']}",
        ]
    )
    logger.error(message)


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
