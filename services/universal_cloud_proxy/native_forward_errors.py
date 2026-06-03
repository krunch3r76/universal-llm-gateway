"""Shared upstream error publishing for native cloud-proxy forwarders."""

from __future__ import annotations

import httpx
from fastapi import HTTPException
from universal_event_bus import EventBus

from .events import CloudProxyRequestFailed


async def publish_failed(
    event_bus: EventBus | None,
    *,
    provider: str,
    model: str,
    status_code: int,
    error: str,
    adapter_type: str,
) -> None:
    if event_bus is None:
        return
    await event_bus.publish_nowait(
        CloudProxyRequestFailed(
            provider=provider,
            model=model,
            status_code=status_code,
            error=error,
            adapter_type=adapter_type,
        )
    )


def status_code_for_native_exc(exc: Exception) -> int:
    """Map upstream/adapter exceptions to HTTP status (ValueError → 500)."""
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code if exc.response else 502
    if isinstance(exc, ValueError):
        return 500
    if isinstance(exc, httpx.HTTPError):
        return 502
    return 502


async def publish_and_raise(
    event_bus: EventBus | None,
    *,
    exc: Exception,
    provider: str,
    model: str,
    adapter_type: str,
) -> None:
    """Emit failure event and raise HTTPException with a unified status mapping."""
    status_code = status_code_for_native_exc(exc)
    error_text = str(exc)[:300]
    await publish_failed(
        event_bus,
        provider=provider,
        model=model,
        status_code=status_code,
        error=error_text,
        adapter_type=adapter_type,
    )
    detail = (
        f"Upstream provider error: {error_text}"
        if isinstance(exc, httpx.HTTPStatusError)
        else error_text
    )
    raise HTTPException(status_code=status_code, detail=detail) from exc
