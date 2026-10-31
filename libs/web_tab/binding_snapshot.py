"""Read-only binding head snapshot — no writers in the eyes slice."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from universal_protocol.status_basis import status_basis


def read_binding_head(
    *,
    house_id: str,
    agent: str | None,
    resolve_payload: dict[str, Any] | None,
) -> dict[str, Any]:
    """Map delivery-bindings resolve JSON into a qualified scalar envelope."""
    now = datetime.now(tz=UTC).isoformat()
    if not resolve_payload:
        return status_basis(
            "binding_state",
            None,
            as_of=now,
            source="binding.unobserved",
            scope=f"house:{house_id}/agent:{agent or '*'}",
            epoch={"kind": "none"},
            stale=True,
        )
    state = resolve_payload.get("state") or resolve_payload.get("status")
    return status_basis(
        "binding_state",
        state,
        as_of=str(resolve_payload.get("as_of") or now),
        source="agent_bus.delivery_bindings.resolve",
        scope=f"house:{house_id}/agent:{agent or '*'}",
        epoch={"row": resolve_payload.get("subscription_id") or resolve_payload.get("id")},
        url=resolve_payload.get("url"),
        conversation_id=resolve_payload.get("conversation_id"),
    )
