"""Server-stamped connector plane observation (not caller claims)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from universal_protocol.status_basis import status_basis


def observe_connector_plane_from_server(
    *,
    house_id: str,
    agent: str,
    caller_identity: str | None,
    oauth_client_id: str | None,
    last_attested_turn: int | None,
    attestation_source: str | None,
) -> dict[str, Any]:
    """Report MCP connector plane from request-scoped stamps and bus head."""
    now = datetime.now(tz=UTC).isoformat()
    scope = f"house:{house_id}/agent:{agent}"
    identity = caller_identity or oauth_client_id
    alive = bool(identity)
    source = attestation_source or (
        "mcp.request_scope" if identity else "connector.unobserved"
    )
    return {
        "plane": "connector",
        "house_id": house_id,
        "agent": agent,
        "connector_alive": status_basis(
            "connector_alive",
            alive,
            as_of=now,
            source=source,
            scope=scope,
            epoch={"caller_identity": caller_identity, "oauth_client_id": oauth_client_id},
        ),
        "last_attested_turn": status_basis(
            "last_attested_turn",
            last_attested_turn,
            as_of=now,
            source=source,
            scope=scope,
            epoch={},
        ),
    }


def latest_bus_attestation(thread_id: str) -> tuple[int | None, str | None]:
    """Read latest turn attestation from the agent-bus store (eyes slice)."""
    try:
        from agent_bus_store.db.turns import get_latest_turn
    except ImportError:
        return None, None
    turn = get_latest_turn(thread_id)
    if not turn:
        return None, None
    turn_no = turn.get("turn_number")
    attested = turn.get("attested_by")
    return (
        int(turn_no) if isinstance(turn_no, int) else None,
        str(attested) if attested else None,
    )
