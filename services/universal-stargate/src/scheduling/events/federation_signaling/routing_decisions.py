"""Federation routing delegation, VRAM probe, and admission gate event signals.

Master routing delegated/local/rejected, inference forwarding, VRAM probe
orchestration, activation-empty, and circuit-breaker rejection.
Imported via the ``federation_signaling`` package facade."""

# ruff: noqa: N802

from universal_event_bus import Event, event_factory

FEDERATION_ROUTING_DELEGATED = "federation.routing.delegated"

FEDERATION_ROUTING_ROUTED_LOCAL = "federation.routing.routed.local"

FEDERATION_ROUTING_REJECTED = "federation.routing.rejected"

FEDERATION_REQUEST_INFERENCE_STARTED_FORWARDED = (
    "federation.request.inference.forwarded"
)

FEDERATION_VRAM_REQUEST_SENT = "federation.vram.request.sent"

FEDERATION_VRAM_REQUEST_FAILED = "federation.vram.request.failed"

FEDERATION_VRAM_RESPONSE_RECEIVED = "federation.vram.response.received"

FEDERATION_ACTIVATION_FILTERED_EMPTY = "federation.activation.filtered.empty"

FEDERATION_CIRCUIT_BREAKER_REQUEST_REJECTED = "federation.circuit.breaker.rejected"


@event_factory
def FederationRoutingDelegated(
    request_id: str,
    target_remote: str,
    model_id: str,
    reason: str | None = None,
) -> Event:
    """Master delegated request to Remote Stargate."""
    payload = {
        "request_id": request_id,
        "target_remote": target_remote,
        "model_id": model_id,
        **({"reason": reason} if reason is not None else {}),
    }
    return Event(signal=FEDERATION_ROUTING_DELEGATED, payload=payload)


@event_factory
def FederationRoutingRoutedLocal(
    request_id: str,
    model_id: str,
    reason: str,
) -> Event:
    """Master routed request to local Gateway."""
    return Event(
        signal=FEDERATION_ROUTING_ROUTED_LOCAL,
        payload={
            "request_id": request_id,
            "model_id": model_id,
            "reason": reason,
        },
    )


@event_factory
def FederationRoutingRejected(
    request_id: str,
    model_id: str,
    reason: str,
) -> Event:
    """Master rejected request (no available target)."""
    return Event(
        signal=FEDERATION_ROUTING_REJECTED,
        payload={
            "request_id": request_id,
            "model_id": model_id,
            "reason": reason,
        },
    )


@event_factory
def FederationRequestInferenceStartedForwarded(
    request_id: str | None,
    peer_count: int,
) -> Event:
    """Emit fanout confirmation for request-start signals forwarded to peers."""
    return Event(
        signal=FEDERATION_REQUEST_INFERENCE_STARTED_FORWARDED,
        payload={"request_id": request_id, "peer_count": peer_count},
    )


@event_factory
def FederationVramRequestSent(
    request_id: str,
    peer_id: str,
    device_index: int,
) -> Event:
    """Track outbound VRAM probe dispatch to peer/device pairing for correlation."""
    return Event(
        signal=FEDERATION_VRAM_REQUEST_SENT,
        payload={
            "request_id": request_id,
            "peer_id": peer_id,
            "device_index": device_index,
        },
    )


@event_factory
def FederationVramRequestFailed(
    request_id: str,
    reason: str,
) -> Event:
    """Record VRAM probe dispatch failure with explicit operational reason."""
    return Event(
        signal=FEDERATION_VRAM_REQUEST_FAILED,
        payload={"request_id": request_id, "reason": reason},
    )


@event_factory
def FederationVramResponseReceived(
    request_id: str,
    matched: bool,
) -> Event:
    """Capture VRAM probe response correlation success or orphaned-response mismatch."""
    return Event(
        signal=FEDERATION_VRAM_RESPONSE_RECEIVED,
        payload={"request_id": request_id, "matched": matched},
    )


@event_factory
def FederationActivationFilteredEmpty(
    gateway_id: str,
    available_count: int,
    activated_count: int,
) -> Event:
    """Gateway has available models but activation list is explicitly empty."""
    return Event(
        signal=FEDERATION_ACTIVATION_FILTERED_EMPTY,
        payload={
            "gateway_id": gateway_id,
            "available_count": available_count,
            "activated_count": activated_count,
        },
    )


@event_factory
def FederationCircuitBreakerRequestRejected(
    gateway_id: str,
    model_id: str,
    reason: str,
) -> Event:
    """Expose circuit-breaker admission guard outcomes for rejected model requests."""
    return Event(
        signal=FEDERATION_CIRCUIT_BREAKER_REQUEST_REJECTED,
        payload={"gateway_id": gateway_id, "model_id": model_id, "reason": reason},
    )
