"""Token-locality guidance-delivery lifecycle event factories (step 2).

Cross-execution observability stream (scope="global", role="observation") for the
agent-workflow-parity token-locality measurement substrate. Payloads carry the
acceptance §4.4 minimum field sets (superset permitted). Emitters are wired by the
unlanded decision-point resolver (Track B); these are definitions only.
"""

from __future__ import annotations

from typing import Any

from universal_event_bus import Event, event_factory


@event_factory
def GuidanceDeliveryRecorded(  # noqa: N802
    execution_id: str,
    guidance_resource_key: str,
    projection_surface: str,
    delivered_tokens: int,
    fetch_scope: str,
    token_category: str,
    *,
    content_digest: str | None = None,
    delivered_bytes: int | None = None,
    is_duplicate: int | None = None,
    dedup_scope: str | None = None,
    request_id: str | None = None,
    dispatch_id: str | None = None,
    registry_schema_version: str | None = None,
    producer_version: str | None = None,
) -> Event:
    """Emitted when a guidance artifact is delivered to a seat."""
    payload: dict[str, Any] = {
        "execution_id": execution_id,
        "guidance_resource_key": guidance_resource_key,
        "projection_surface": projection_surface,
        "delivered_tokens": delivered_tokens,
        "fetch_scope": fetch_scope,
        "token_category": token_category,
    }
    if content_digest:
        payload["content_digest"] = content_digest
    if delivered_bytes is not None:
        payload["delivered_bytes"] = delivered_bytes
    if is_duplicate is not None:
        payload["is_duplicate"] = is_duplicate
    if dedup_scope:
        payload["dedup_scope"] = dedup_scope
    if request_id:
        payload["request_id"] = request_id
    if dispatch_id:
        payload["dispatch_id"] = dispatch_id
    if registry_schema_version:
        payload["registry_schema_version"] = registry_schema_version
    if producer_version:
        payload["producer_version"] = producer_version
    return Event(
        signal="guidance.delivery.recorded",
        payload=payload,
        role="observation",
        scope="global",
    )


@event_factory
def GuidanceDeliveryDeduped(  # noqa: N802
    execution_id: str,
    guidance_resource_key: str,
    trigger_fan_in_count: int,
    *,
    dedup_scope: str | None = None,
    request_id: str | None = None,
    dispatch_id: str | None = None,
    registry_schema_version: str | None = None,
    producer_version: str | None = None,
) -> Event:
    """Emitted when the resolver collapses overlapping triggers into one bundle."""
    payload: dict[str, Any] = {
        "execution_id": execution_id,
        "guidance_resource_key": guidance_resource_key,
        "trigger_fan_in_count": trigger_fan_in_count,
    }
    if dedup_scope:
        payload["dedup_scope"] = dedup_scope
    if request_id:
        payload["request_id"] = request_id
    if dispatch_id:
        payload["dispatch_id"] = dispatch_id
    if registry_schema_version:
        payload["registry_schema_version"] = registry_schema_version
    if producer_version:
        payload["producer_version"] = producer_version
    return Event(
        signal="guidance.delivery.deduped",
        payload=payload,
        role="observation",
        scope="global",
    )


@event_factory
def GuidanceRestatementDetected(  # noqa: N802
    execution_id: str,
    guidance_resource_key: str,
    restated_overlap_tokens: int,
    *,
    request_id: str | None = None,
    dispatch_id: str | None = None,
    registry_schema_version: str | None = None,
    producer_version: str | None = None,
) -> Event:
    """Emitted when restated-guidance overlap is found at closeout."""
    payload: dict[str, Any] = {
        "execution_id": execution_id,
        "guidance_resource_key": guidance_resource_key,
        "restated_overlap_tokens": restated_overlap_tokens,
    }
    if request_id:
        payload["request_id"] = request_id
    if dispatch_id:
        payload["dispatch_id"] = dispatch_id
    if registry_schema_version:
        payload["registry_schema_version"] = registry_schema_version
    if producer_version:
        payload["producer_version"] = producer_version
    return Event(
        signal="guidance.restatement.detected",
        payload=payload,
        role="observation",
        scope="global",
    )


@event_factory
def GuidanceWorkflowSummarized(  # noqa: N802
    execution_id: str,
    workflow_class: str,
    phase: str,
    token_vector: dict[str, int],
    *,
    campaign_id: str | None = None,
    seat_substrate: str | None = None,
    request_id: str | None = None,
    dispatch_id: str | None = None,
    registry_schema_version: str | None = None,
    producer_version: str | None = None,
) -> Event:
    """Emitted when a per-workflow rollup occurs at workflow/session close."""
    payload: dict[str, Any] = {
        "execution_id": execution_id,
        "workflow_class": workflow_class,
        "phase": phase,
        "token_vector": token_vector,
    }
    if campaign_id:
        payload["campaign_id"] = campaign_id
    if seat_substrate:
        payload["seat_substrate"] = seat_substrate
    if request_id:
        payload["request_id"] = request_id
    if dispatch_id:
        payload["dispatch_id"] = dispatch_id
    if registry_schema_version:
        payload["registry_schema_version"] = registry_schema_version
    if producer_version:
        payload["producer_version"] = producer_version
    return Event(
        signal="guidance.workflow.summarized",
        payload=payload,
        role="observation",
        scope="global",
    )
