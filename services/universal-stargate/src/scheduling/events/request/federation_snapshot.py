"""Edge→Master federation snapshot broadcast event signal.

Historically owned by the request module; kept here per Opus bind.
Imported via the ``request`` package facade."""

# ruff: noqa: N802

from universal_event_bus import Event, event_factory

FEDERATION_SNAPSHOT_SENT = "federation.snapshot.sent"
"""
Edge Stargate sent GATEWAY_SNAPSHOT to Master.

Payload documents all_models vs available_models gap — the difference
between what /v1/models shows and what Master can actually route.

Diagnostic query:
    jq 'select(.signal == "federation.snapshot.sent" and .payload.gap_count > 0)'

Payload: {
    "gateway_id": str,
    "all_models_count": int,     # from ws_client.get_models()
    "available_models_count": int, # models WITH resource data (routable)
    "gap_count": int,            # all_models_count - available_models_count
}
"""


@event_factory
def FederationSnapshotSent(
    gateway_id: str,
    all_models_count: int,
    available_models_count: int,
    trigger: str = "initial",
) -> Event:
    """
    Create FEDERATION_SNAPSHOT_SENT event.

    Emitted by Edge Stargate when it broadcasts GATEWAY_SNAPSHOT to Master.
    Documents the gap between all models (visible in /v1/models) and
    routable models (those with resource data in model_details).

    A non-zero gap_count means some models will route as MODEL_NOT_FOUND
    despite appearing in /v1/models — see gateway.snapshot.resource.gap
    in the Edge Gateway events for root cause.

    Args:
        gateway_id: Gateway identifier
        all_models_count: Total models from ws_client.get_models()
        available_models_count: Models with resource data (routable by Master)
        trigger: What caused this snapshot ("initial" at wiring, "periodic"
                 from reconciliation timer)

    Returns:
        Event with FederationSnapshotSent signal
    """
    return Event(
        signal=FEDERATION_SNAPSHOT_SENT,
        payload={
            "gateway_id": gateway_id,
            "all_models_count": all_models_count,
            "available_models_count": available_models_count,
            "gap_count": all_models_count - available_models_count,
            "trigger": trigger,
        },
    )
