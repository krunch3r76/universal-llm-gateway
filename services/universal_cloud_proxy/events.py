from __future__ import annotations

from universal_event_bus import Event, event_factory


@event_factory
def CloudProxyStarted() -> Event:  # noqa: N802
    return Event(signal="cloud.proxy.started", payload={})


@event_factory
def CloudProxyShutdown() -> Event:  # noqa: N802
    return Event(signal="cloud.proxy.shutdown", payload={})


@event_factory
def CloudProxyCatalogRefreshed(  # noqa: N802
    *,
    provider: str,
    model_count: int,
) -> Event:
    return Event(
        signal="cloud.proxy.catalog.refreshed",
        payload={"provider": provider, "model_count": model_count},
    )


@event_factory
def CloudProxyRequestForwarded(  # noqa: N802
    *,
    provider: str,
    model: str,
    streaming: bool,
) -> Event:
    return Event(
        signal="cloud.proxy.request.forwarded",
        payload={"provider": provider, "model": model, "streaming": streaming},
    )


@event_factory
def CloudProxyRequestFailed(  # noqa: N802
    *,
    provider: str,
    model: str,
    status_code: int,
    error: str,
) -> Event:
    return Event(
        signal="cloud.proxy.request.failed",
        payload={
            "provider": provider,
            "model": model,
            "status_code": status_code,
            "error": error,
        },
    )
