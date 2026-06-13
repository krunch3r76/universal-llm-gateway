"""B3 delivery-audit registry lifecycle event factories.

Node-scoped signals (``scope="node"``) for parent lifecycle visibility and
registry write failures. Registry state lives in ``delivery-audit.db``; these
events surface lifecycle and failure signals through Event Service.
"""

from __future__ import annotations

from universal_event_bus import Event, event_factory


@event_factory
def DeliveryAuditParentOpened(  # noqa: N802
    audit_id: str,
    *,
    execution_id: str | None = None,
    request_id: str | None = None,
    dispatch_id: str | None = None,
    registry_schema_version: str | None = None,
    producer_version: str | None = None,
) -> Event:
    """Emitted when a B3 delivery-audit parent row is opened."""
    payload: dict[str, str] = {"audit_id": audit_id}
    if execution_id:
        payload["execution_id"] = execution_id
    if request_id:
        payload["request_id"] = request_id
    if dispatch_id:
        payload["dispatch_id"] = dispatch_id
    if registry_schema_version:
        payload["registry_schema_version"] = registry_schema_version
    if producer_version:
        payload["producer_version"] = producer_version
    return Event(
        signal="delivery.audit.parent.opened",
        payload=payload,
        scope="node",
    )


@event_factory
def DeliveryAuditParentFinalized(  # noqa: N802
    audit_id: str,
    aggregate_audit_status: str,
    *,
    execution_id: str | None = None,
    request_id: str | None = None,
    dispatch_id: str | None = None,
    registry_schema_version: str | None = None,
    producer_version: str | None = None,
) -> Event:
    """Emitted when a B3 delivery-audit parent row is finalized."""
    payload: dict[str, str] = {
        "audit_id": audit_id,
        "aggregate_audit_status": aggregate_audit_status,
    }
    if execution_id:
        payload["execution_id"] = execution_id
    if request_id:
        payload["request_id"] = request_id
    if dispatch_id:
        payload["dispatch_id"] = dispatch_id
    if registry_schema_version:
        payload["registry_schema_version"] = registry_schema_version
    if producer_version:
        payload["producer_version"] = producer_version
    return Event(
        signal="delivery.audit.parent.finalized",
        payload=payload,
        scope="node",
    )


@event_factory
def DeliveryAuditRegistryWriteFailed(  # noqa: N802
    *,
    audit_id: str | None = None,
    execution_id: str | None = None,
    request_id: str | None = None,
    dispatch_id: str | None = None,
    error_code: str | None = None,
    error: str | None = None,
    registry_schema_version: str | None = None,
    producer_version: str | None = None,
) -> Event:
    """Emitted when a delivery-audit registry write fails before persisting state."""
    payload: dict[str, str] = {}
    if audit_id:
        payload["audit_id"] = audit_id
    if execution_id:
        payload["execution_id"] = execution_id
    if request_id:
        payload["request_id"] = request_id
    if dispatch_id:
        payload["dispatch_id"] = dispatch_id
    if error_code:
        payload["error_code"] = error_code
    if error:
        payload["error"] = error
    if registry_schema_version:
        payload["registry_schema_version"] = registry_schema_version
    if producer_version:
        payload["producer_version"] = producer_version
    return Event(
        signal="delivery.audit.registry.write.failed",
        payload=payload,
        scope="node",
    )
