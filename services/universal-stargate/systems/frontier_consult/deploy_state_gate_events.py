"""Deploy-state gate observability signals."""

from __future__ import annotations

from universal_event_bus import Event, event_factory

from .cursor_sdk_generate_signals import publish_frontier_event


@event_factory
def DeployStateGateDualReadMismatch(  # noqa: N802
    producer: str,
    field: str,
    get_value: str,
    manage_value: str,
    request_id: str = "",
) -> Event:
    """Manage vs producer GET disagree on a deploy-identity field."""
    payload: dict[str, str] = {
        "producer": producer,
        "field": field,
        "get_value": get_value,
        "manage_value": manage_value,
        "request_id": request_id,
    }
    return Event(
        signal="deploy_state_gate.dual_read_mismatch",
        payload=payload,
        scope="node",
    )


def emit_deploy_state_gate_dual_read_mismatch(
    *,
    producer: str,
    field: str,
    get_value: str,
    manage_value: str,
    request_id: str = "",
) -> None:
    publish_frontier_event(
        DeployStateGateDualReadMismatch(
            producer=producer,
            field=field,
            get_value=get_value,
            manage_value=manage_value,
            request_id=request_id,
        )
    )


__all__ = [
    "DeployStateGateDualReadMismatch",
    "emit_deploy_state_gate_dual_read_mismatch",
]
