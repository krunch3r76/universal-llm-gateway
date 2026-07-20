"""Federation telemetry flow and resource/lifecycle relay event signals.

Covers telemetry receive/stale/apply/wire plus model-lifecycle and resource
updates and federated gateway removal. Imported via the package facade."""

# ruff: noqa: N802

from typing import Any

from universal_event_bus import Event, event_factory

FEDERATION_TELEMETRY_RECEIVED = "federation.telemetry.received"

FEDERATION_TELEMETRY_MARKED_STALE = "federation.telemetry.marked.stale"

FEDERATION_TELEMETRY_APPLIED = "federation.telemetry.applied"

FEDERATION_TELEMETRY_WIRED = "federation.telemetry.wired"

FEDERATION_MODEL_LIFECYCLE_EVENT = "federation.model.lifecycle"

FEDERATION_RESOURCE_UPDATED = "federation.resource.updated"

FEDERATED_GATEWAY_REMOVED = "federation.gateway.removed"


@event_factory
def FederationTelemetryReceived(
    remote_id: str,
    model_count: int,
    resource_summary: dict[str, Any],
    telemetry_age_ms: int | None = None,
    msg_type: str | None = None,
    catalog_model_count: int | None = None,
    loaded_model_count: int | None = None,
    count_source: str | None = None,
) -> Event:
    """Master received telemetry from Remote/Edge.

    Disambiguation fields (msg_type, catalog_model_count, loaded_model_count,
    count_source) clarify what model_count represents per message type.
    """
    payload = {
        "remote_id": remote_id,
        "model_count": model_count,
        "resource_summary": resource_summary,
        **(
            {"telemetry_age_ms": telemetry_age_ms}
            if telemetry_age_ms is not None
            else {}
        ),
        **({"msg_type": msg_type} if msg_type is not None else {}),
        **(
            {"catalog_model_count": catalog_model_count}
            if catalog_model_count is not None
            else {}
        ),
        **(
            {"loaded_model_count": loaded_model_count}
            if loaded_model_count is not None
            else {}
        ),
        **({"count_source": count_source} if count_source is not None else {}),
    }
    return Event(signal=FEDERATION_TELEMETRY_RECEIVED, payload=payload)


@event_factory
def FederationTelemetryMarkedStale(
    remote_id: str,
    age_seconds: float,
    threshold_seconds: float,
) -> Event:
    """Telemetry from Remote exceeded staleness threshold."""
    return Event(
        signal=FEDERATION_TELEMETRY_MARKED_STALE,
        payload={
            "remote_id": remote_id,
            "age_seconds": age_seconds,
            "threshold_seconds": threshold_seconds,
        },
    )


@event_factory
def FederationTelemetryApplied(
    remote_id: str,
    changes: list[str],
) -> Event:
    """Telemetry applied to Master state."""
    return Event(
        signal=FEDERATION_TELEMETRY_APPLIED,
        payload={"remote_id": remote_id, "changes": changes},
    )


@event_factory
def FederationTelemetryWired(
    gateway_url: str,
    gateway_id: str,
) -> Event:
    """Confirm telemetry bridge wiring so master-side freshness waits can proceed."""
    return Event(
        signal=FEDERATION_TELEMETRY_WIRED,
        payload={"gateway_url": gateway_url, "gateway_id": gateway_id},
    )


@event_factory
def FederationModelLifecycleEvent(
    gateway_id: str,
    msg_type: str,
    model_id: str,
) -> Event:
    """Capture lifecycle telemetry application for model-state reconciliation traces."""
    return Event(
        signal=FEDERATION_MODEL_LIFECYCLE_EVENT,
        payload={
            "gateway_id": gateway_id,
            "msg_type": msg_type,
            "model_id": model_id,
        },
        scope="node",
    )


@event_factory
def FederationResourceUpdated(
    gateway_id: str,
    vram_free_mb: int,
    ram_free_mb: int,
) -> Event:
    """Capture resource refresh used by admission and routing feasibility logic."""
    return Event(
        signal=FEDERATION_RESOURCE_UPDATED,
        payload={
            "gateway_id": gateway_id,
            "vram_free_mb": vram_free_mb,
            "ram_free_mb": ram_free_mb,
        },
    )


@event_factory
def FederatedGatewayRemoved(
    gateway_id: str,
    remote_id: str,
) -> Event:
    """Record remote disconnect teardown after gateway removal from manager state."""
    return Event(
        signal=FEDERATED_GATEWAY_REMOVED,
        payload={"gateway_id": gateway_id, "remote_id": remote_id},
    )
