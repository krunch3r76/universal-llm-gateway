"""Exception event factory and capture context manager for the universal event bus.

Provides a shared ExceptionCaught event factory and capture_exception async context
manager so any service can emit structured exception telemetry to the Event Service.
Exceptions become queryable alongside request traces and pipeline events without
coupling exception handling to logging infrastructure.

Caller invariants:
- capture_exception requires a running event loop (all service entry points do)
- Bus publish errors are swallowed to ensure the original exception is never masked
"""

from __future__ import annotations

import traceback as tb_module
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from .event import Event
from .event_bus import EventBus
from .factory import event_factory

_MAX_MSG_LEN = 500
_MAX_TB_LEN = 1000


@event_factory
def ExceptionCaught(  # noqa: N802
    *,
    exception_type: str,
    message: str,
    service: str,
    handler: str = "",
    request_id: str = "",
    traceback: str = "",
) -> Event:
    """Create a system.exception observation event for a caught exception.

    Emits structured exception telemetry to the Event Service so exceptions are
    queryable alongside request traces and pipeline events. Callers may pre-truncate
    string fields to avoid oversized payloads; the capture_exception context
    manager does this automatically.

    Args:
        exception_type: Class name of the exception, e.g. 'ValueError'.
        message: str(exc) truncated to _MAX_MSG_LEN characters.
        service: Originating service name, e.g. 'cloud_proxy', 'stargate', 'rag'.
        handler: Optional function or class name where the exception was caught.
        request_id: Optional request correlation ID if inside a request context.
        traceback: Optional last frames from traceback.format_exc(), truncated to
            _MAX_TB_LEN.

    Returns:
        Event with signal='system.exception', role='observation', scope='global'.
    """
    payload: dict[str, str] = {
        "exception_type": exception_type,
        "message": message,
        "service": service,
    }
    if handler:
        payload["handler"] = handler
    if request_id:
        payload["request_id"] = request_id
    if traceback:
        payload["traceback"] = traceback
    return Event(
        signal="system.exception",
        payload=payload,
        role="observation",
        scope="global",
    )


@asynccontextmanager
async def capture_exception(
    service: str,
    handler: str = "",
    request_id: str = "",
    *,
    event_bus: EventBus,
    reraise: bool = True,
) -> AsyncGenerator[None, None]:
    """Async context manager that emits a system.exception event on Exception.

    Catches Exception (not BaseException — KeyboardInterrupt, SystemExit, and
    GeneratorExit are not intercepted). On exception, builds an ExceptionCaught
    event from the live exception context and publishes it via
    event_bus.publish_nowait() before optionally re-raising.

    Bus publish errors are silently swallowed so the original exception is never
    masked by event bus failures.

    Requires a running asyncio event loop; all service entry points satisfy this.

    Args:
        service: Originating service name passed to ExceptionCaught.
        handler: Optional function or class name at the capture site.
        request_id: Optional request correlation ID for join with request traces.
        event_bus: EventBus instance used to publish the exception event.
        reraise: If True (default), re-raise the caught Exception after publishing.

    Example:
        async with capture_exception(
            "stargate", handler="proxy_request", event_bus=bus
        ):
            await do_risky_operation()
    """
    try:
        yield
    except Exception as exc:
        tb_str = tb_module.format_exc()
        event = ExceptionCaught(
            exception_type=type(exc).__name__,
            message=str(exc)[:_MAX_MSG_LEN],
            service=service,
            handler=handler,
            request_id=request_id,
            traceback=tb_str[:_MAX_TB_LEN],
        )
        try:
            await event_bus.publish_nowait(event)
        except Exception:
            pass  # ∀ bus error: ¬mask original exception
        if reraise:
            raise
