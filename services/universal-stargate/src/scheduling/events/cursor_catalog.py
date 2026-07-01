"""Cursor SDK catalog event signals (Stargate-side observation).

Signals:
    cursor.catalog.drift.detected — live catalog diverges from descriptors
    cursor.catalog.updated — catalog re-fetched and virtual gateway updated
    cursor.catalog.fetch.failed — catalog fetch from worker failed
    cursor.catalog.unavailable — worker health probe failed
    cursor.catalog.available — worker reachable and catalog registered
"""

from universal_event_bus import Event, event_factory

CURSOR_CATALOG_AVAILABLE = "cursor.catalog.available"
CURSOR_CATALOG_UNAVAILABLE = "cursor.catalog.unavailable"
CURSOR_CATALOG_UPDATED = "cursor.catalog.updated"
CURSOR_CATALOG_FETCH_FAILED = "cursor.catalog.fetch.failed"
CURSOR_CATALOG_DRIFT_DETECTED = "cursor.catalog.drift.detected"


@event_factory
def CursorCatalogAvailable(worker_url: str, model_count: int) -> Event:  # noqa: N802
    return Event(
        signal=CURSOR_CATALOG_AVAILABLE,
        payload={"worker_url": worker_url, "model_count": model_count},
    )


@event_factory
def CursorCatalogUnavailable(worker_url: str, reason: str) -> Event:  # noqa: N802
    return Event(
        signal=CURSOR_CATALOG_UNAVAILABLE,
        payload={"worker_url": worker_url, "reason": reason},
    )


@event_factory
def CursorCatalogUpdated(worker_url: str, model_count: int) -> Event:  # noqa: N802
    return Event(
        signal=CURSOR_CATALOG_UPDATED,
        payload={"worker_url": worker_url, "model_count": model_count},
    )


@event_factory
def CursorCatalogFetchFailed(worker_url: str, error: str) -> Event:  # noqa: N802
    return Event(
        signal=CURSOR_CATALOG_FETCH_FAILED,
        payload={"worker_url": worker_url, "error": error},
    )


@event_factory
def CursorCatalogDriftDetected(  # noqa: N802
    worker_url: str,
    divergence_count: int,
    sample: list[str],
) -> Event:
    return Event(
        signal=CURSOR_CATALOG_DRIFT_DETECTED,
        payload={
            "worker_url": worker_url,
            "divergence_count": divergence_count,
            "sample": sample,
        },
    )
