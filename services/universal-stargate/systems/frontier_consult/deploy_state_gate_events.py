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
) -> Event:
    """Manage vs producer GET disagree on a deploy-identity field."""
    return Event(
        signal="deploy_state_gate.dual_read_mismatch",
        payload={
            "producer": producer,
            "field": field,
            "get_value": get_value,
            "manage_value": manage_value,
        },
        scope="node",
    )


def emit_deploy_state_gate_dual_read_mismatch(
    *,
    producer: str,
    field: str,
    get_value: str,
    manage_value: str,
) -> None:
    publish_frontier_event(
        DeployStateGateDualReadMismatch(
            producer=producer,
            field=field,
            get_value=get_value,
            manage_value=manage_value,
        )
    )


__all__ = [
    "DeployStateGateDualReadMismatch",
    "emit_deploy_state_gate_dual_read_mismatch",
]
