"""Federation loading lease lifecycle signals."""

# ruff: noqa: N802

from universal_event_bus import Event, event_factory

FEDERATION_LOADING_LEASE_EXPIRED = "federation.loading.lease.expired"


@event_factory
def FederationLoadingLeaseExpired(
    gateway_id: str,
    model_id: str,
    reason: str,
    age_s: float,
    origin: str,
) -> Event:
    """Master dropped a loading lease without a terminal load event."""
    return Event(
        signal=FEDERATION_LOADING_LEASE_EXPIRED,
        payload={
            "gateway_id": gateway_id,
            "model_id": model_id,
            "reason": reason,
            "age_s": age_s,
            "origin": origin,
        },
    )
