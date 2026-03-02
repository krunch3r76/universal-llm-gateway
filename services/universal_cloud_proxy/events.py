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


@event_factory
def CloudProxyBrowserCatalogRefreshed(  # noqa: N802
    *,
    trigger: str,
    model_count: int,
) -> Event:
    return Event(
        signal="cloud.proxy.browser.catalog.refreshed",
        payload={
            "trigger": trigger,
            "model_count": model_count,
        },
    )


@event_factory
def CloudProxyBrowserCatalogRefreshFailed(  # noqa: N802
    *,
    trigger: str,
    error: str,
) -> Event:
    return Event(
        signal="cloud.proxy.browser.refresh.failed",
        payload={
            "trigger": trigger,
            "error": error,
        },
    )


@event_factory
def CloudProxyBrowserModelLookupMiss(  # noqa: N802
    *,
    model_id: str,
) -> Event:
    return Event(
        signal="cloud.proxy.browser.lookup.miss",
        payload={"model_id": model_id},
    )


@event_factory
def CloudProxyBrowserUiUnavailable(  # noqa: N802
    *,
    missing_files: list[str],
) -> Event:
    return Event(
        signal="cloud.proxy.browser.ui.unavailable",
        payload={"missing_files": missing_files},
    )


@event_factory
def CloudProxyBrowserSelectCompleted(  # noqa: N802
    *,
    selected_count: int,
    tags: list[str],
    exclude_tags: list[str],
    sort_by: str,
    min_context: int,
    modality_contains: str | None,
    max_completion_cost: float | None,
    auto_excluded_multimodal: bool,
) -> Event:
    return Event(
        signal="cloud.proxy.browser.select.completed",
        payload={
            "selected_count": selected_count,
            "tags": tags,
            "exclude_tags": exclude_tags,
            "sort_by": sort_by,
            "min_context": min_context,
            "modality_contains": modality_contains,
            "max_completion_cost": max_completion_cost,
            "auto_excluded_multimodal": auto_excluded_multimodal,
        },
    )


@event_factory
def CloudProxyBrowserSelectFailed(  # noqa: N802
    *,
    error: str,
) -> Event:
    return Event(
        signal="cloud.proxy.browser.select.failed",
        payload={"error": error},
    )


@event_factory
def CloudProxyLocalCatalogRefreshed(  # noqa: N802
    *,
    stargate_url: str,
    model_count: int,
) -> Event:
    return Event(
        signal="cloud.proxy.local.catalog.refreshed",
        payload={"stargate_url": stargate_url, "model_count": model_count},
    )


@event_factory
def CloudProxyLocalCatalogUnavailable(  # noqa: N802
    *,
    stargate_url: str,
    error: str,
) -> Event:
    return Event(
        signal="cloud.proxy.local.catalog.unavailable",
        payload={"stargate_url": stargate_url, "error": error},
    )
