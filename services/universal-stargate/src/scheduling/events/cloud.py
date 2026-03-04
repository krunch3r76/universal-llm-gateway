"""Cloud proxy event signals (Stargate-side observation).

Covers Stargate's observation of the cloud proxy at the coordination boundary:
availability, catalog updates, and fetch failures.

Signals:
    cloud.proxy.available — proxy became reachable, catalog fetched
    cloud.proxy.unavailable — proxy health probe failed
    cloud.proxy.catalog.updated — proxy catalog re-fetched, virtual gateways updated
    cloud.proxy.catalog.fetch.failed — catalog fetch from proxy failed
"""

from universal_event_bus import Event, event_factory

# ========================================
# Cloud Proxy Event Signals
# ========================================

# Cloud proxy availability (Stargate-side observation of the proxy)
CLOUD_PROXY_AVAILABLE = "cloud.proxy.available"
CLOUD_PROXY_UNAVAILABLE = "cloud.proxy.unavailable"
CLOUD_PROXY_CATALOG_UPDATED = "cloud.proxy.catalog.updated"
CLOUD_PROXY_CATALOG_FETCH_FAILED = "cloud.proxy.catalog.fetch.failed"


# ========================================
# Factory Functions
# ========================================


@event_factory
def CloudProxyAvailable(proxy_url: str, model_count: int) -> Event:  # noqa: N802
    """
    Proxy became reachable and catalog was fetched.

    Args:
        proxy_url: Cloud proxy URL
        model_count: Number of models returned by proxy catalog

    Returns:
        Event with CloudProxyAvailable signal
    """
    return Event(
        signal=CLOUD_PROXY_AVAILABLE,
        payload={"proxy_url": proxy_url, "model_count": model_count},
    )


@event_factory
def CloudProxyUnavailable(  # noqa: N802
    proxy_url: str,
    reason: str,
    detection_method: str | None = None,
) -> Event:
    """
    Proxy health probe failed — no cloud models will be registered.

    Args:
        proxy_url: Cloud proxy URL
        reason: Failure reason from health probe path
        detection_method: Detection mode (`socket_missing`, `health_probe_failed`,
            or None for legacy callers)

    Returns:
        Event with CloudProxyUnavailable signal
    """
    return Event(
        signal=CLOUD_PROXY_UNAVAILABLE,
        payload={
            "proxy_url": proxy_url,
            "reason": reason,
            "detection_method": detection_method,
        },
    )


@event_factory
def CloudProxyCatalogUpdated(  # noqa: N802
    proxy_url: str, model_count: int, gateway_count: int
) -> Event:
    """
    Proxy catalog was re-fetched and virtual gateways were updated.

    Args:
        proxy_url: Cloud proxy URL
        model_count: Number of models in latest catalog
        gateway_count: Number of synthesized virtual gateways

    Returns:
        Event with CloudProxyCatalogUpdated signal
    """
    return Event(
        signal=CLOUD_PROXY_CATALOG_UPDATED,
        payload={
            "proxy_url": proxy_url,
            "model_count": model_count,
            "gateway_count": gateway_count,
        },
    )


@event_factory
def CloudProxyCatalogFetchFailed(proxy_url: str, error: str) -> Event:  # noqa: N802
    """
    Failed to fetch catalog from cloud proxy.

    Args:
        proxy_url: Cloud proxy URL
        error: Error message from fetch attempt

    Returns:
        Event with CloudProxyCatalogFetchFailed signal
    """
    return Event(
        signal=CLOUD_PROXY_CATALOG_FETCH_FAILED,
        payload={"proxy_url": proxy_url, "error": error},
    )
