from __future__ import annotations

from universal_event_bus import Event, event_factory


@event_factory
def CloudProxyStarted(  # noqa: N802
    *,
    pid: int,
    mode: str,
    socket_path: str | None,
) -> Event:
    """Emit cloud proxy startup lifecycle event."""
    return Event(
        signal="cloud.proxy.started",
        payload={"pid": pid, "mode": mode, "socket_path": socket_path},
    )


@event_factory
def CloudProxyShutdown(*, reason: str) -> Event:  # noqa: N802
    """Emit cloud proxy shutdown lifecycle event.

    reason: 'clean' for graceful shutdown, 'crash' for unexpected termination
    (e.g. atexit without clean flag set).
    """
    return Event(signal="cloud.proxy.shutdown", payload={"reason": reason})


@event_factory
def CloudProxyCatalogRefreshed(  # noqa: N802
    *,
    provider: str,
    model_count: int,
) -> Event:
    """Emit successful provider catalog refresh event."""
    return Event(
        signal="cloud.proxy.catalog.refreshed",
        payload={"provider": provider, "model_count": model_count},
    )


@event_factory
def CloudProxyCatalogRefreshFailed(  # noqa: N802
    *,
    provider: str,
    error: str,
) -> Event:
    """Emit failed provider catalog refresh event."""
    return Event(
        signal="cloud.proxy.catalog.refresh.failed",
        payload={"provider": provider, "error": error},
    )


@event_factory
def CloudProxyRequestForwarded(  # noqa: N802
    *,
    provider: str,
    model: str,
    streaming: bool,
    adapter_type: str,
) -> Event:
    """Emit request forwarded event for cloud provider calls."""
    return Event(
        signal="cloud.proxy.request.forwarded",
        payload={
            "provider": provider,
            "model": model,
            "streaming": streaming,
            "adapter_type": adapter_type,
        },
    )


@event_factory
def CloudProxyRequestFailed(  # noqa: N802
    *,
    provider: str,
    model: str,
    status_code: int,
    error: str,
    adapter_type: str,
) -> Event:
    """Emit request failure event for cloud provider calls."""
    return Event(
        signal="cloud.proxy.request.failed",
        payload={
            "provider": provider,
            "model": model,
            "status_code": status_code,
            "error": error,
            "adapter_type": adapter_type,
        },
    )


@event_factory
def CloudProxyRequestTranslationFailed(  # noqa: N802
    *,
    provider: str,
    model: str,
    error: str,
    direction: str,
    adapter_type: str,
) -> Event:
    """Emit request/response translation failures from provider adapters."""
    return Event(
        signal="cloud.proxy.request.translation.failed",
        payload={
            "provider": provider,
            "model": model,
            "error": error,
            "direction": direction,
            "adapter_type": adapter_type,
        },
    )


@event_factory
def CloudProxyBrowserCatalogRefreshed(  # noqa: N802
    *,
    trigger: str,
    model_count: int,
) -> Event:
    """Emit successful browser catalog refresh event."""
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
    """Emit failed browser catalog refresh event."""
    return Event(
        signal="cloud.proxy.browser.catalog.refresh.failed",
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
    """Emit browser lookup miss event for model IDs."""
    return Event(
        signal="cloud.proxy.browser.lookup.miss",
        payload={"model_id": model_id},
    )


@event_factory
def CloudProxyBrowserUiUnavailable(  # noqa: N802
    *,
    missing_files: list[str],
) -> Event:
    """Emit browser UI unavailable event for missing assets."""
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
    auto_excluded_multimodal: bool = False,
) -> Event:
    """Emit successful browser model selection event."""
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
    """Emit failed browser model selection event."""
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
    """Emit successful local catalog refresh event."""
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
    """Emit local catalog unavailable event."""
    return Event(
        signal="cloud.proxy.local.catalog.unavailable",
        payload={"stargate_url": stargate_url, "error": error},
    )


@event_factory
def CloudProxyMcpConfigured(  # noqa: N802
    *,
    provider: str,
    mcp_server_url: str,
) -> Event:
    """Emit MCP server configured event at startup for providers with mcp_server_url set."""
    return Event(
        signal="cloud.proxy.mcp.configured",
        payload={"provider": provider, "mcp_server_url": mcp_server_url},
    )


@event_factory
def McpAdapterV2Configured(  # noqa: N802
    *,
    provider: str,
    server_name: str,
    always_loaded_count: int,
    deferred_count: int,
) -> Event:
    """First request with mcp_v2=true built the toolset payload."""
    return Event(
        signal="mcp.adapter.v2.configured",
        payload={
            "provider": provider,
            "server_name": server_name,
            "always_loaded_count": always_loaded_count,
            "deferred_count": deferred_count,
        },
    )


@event_factory
def McpAdapterRequestShape(  # noqa: N802
    *,
    provider: str,
    model: str,
    mcp_version: str,
    tool_count: int,
    mcp_tool_count: int,
    has_tool_search: bool,
) -> Event:
    """Every MCP request — shape summary for v1/v2 migration tracking."""
    return Event(
        signal="mcp.adapter.request.shape",
        payload={
            "provider": provider,
            "model": model,
            "mcp_version": mcp_version,
            "tool_count": tool_count,
            "mcp_tool_count": mcp_tool_count,
            "has_tool_search": has_tool_search,
        },
    )


@event_factory
def McpAdapterMcpToolUseSeen(  # noqa: N802
    *,
    tool_name: str,
    server_name: str,
) -> Event:
    """Response contained an mcp_tool_use block (Anthropic-executed MCP tool)."""
    return Event(
        signal="mcp.adapter.tool.seen",
        payload={"tool_name": tool_name, "server_name": server_name},
    )


@event_factory
def McpAdapterToolSearchSeen(  # noqa: N802
    *,
    references_count: int,
) -> Event:
    """Response contained a tool_search_tool_result block."""
    return Event(
        signal="mcp.adapter.search.seen",
        payload={"references_count": references_count},
    )
