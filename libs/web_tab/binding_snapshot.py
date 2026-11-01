"""Read-only binding head snapshot — no writers in the eyes slice."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from universal_protocol.status_basis import status_basis

from web_tab.conversation_id import conversation_id_from_url


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
    current = resolve_payload.get("current")
    actuation = current.get("actuation") if isinstance(current, dict) else None
    if not isinstance(actuation, dict):
        actuation = {}
    url = actuation.get("conversation_url")
    url_text = url.strip() if isinstance(url, str) else ""
    cid = actuation.get("conversation_id")
    conversation_id = cid.strip() if isinstance(cid, str) and cid.strip() else None
    if conversation_id is None and url_text:
        conversation_id = conversation_id_from_url(url_text)
    row = current.get("subscription_id") if isinstance(current, dict) else None
    return status_basis(
        "binding_state",
        state,
        as_of=str(resolve_payload.get("as_of") or now),
        source="agent_bus.delivery_bindings.resolve",
        scope=f"house:{house_id}/agent:{agent or '*'}",
        epoch={"row": row},
        url=url_text or None,
        conversation_id=conversation_id,
    )
