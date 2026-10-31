"""Connector vs actuation plane observation."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from universal_protocol.status_basis import status_basis

from web_tab.cdp_port import CdpObservationPort
from web_tab.conversation_id import conversation_id_from_url


@dataclass(frozen=True)
class ConnectorPlaneInput:
    """Caller-supplied connector-plane facts (OAuth attestation path)."""

    connector_alive: bool | None
    last_attested_turn: int | None
    attestation_source: str | None


def observe_connector_plane(
    *,
    house_id: str,
    agent: str,
    conn: ConnectorPlaneInput,
) -> dict[str, Any]:
    """Report MCP connector plane separately from browser actuation."""
    now = datetime.now(tz=UTC).isoformat()
    alive = conn.connector_alive
    turn = conn.last_attested_turn
    return {
        "plane": "connector",
        "house_id": house_id,
        "agent": agent,
        "connector_alive": status_basis(
            "connector_alive",
            alive,
            as_of=now,
            source=conn.attestation_source or "connector.unobserved",
            scope=f"house:{house_id}/agent:{agent}",
            epoch={"last_attested_turn": turn},
        ),
        "last_attested_turn": status_basis(
            "last_attested_turn",
            turn,
            as_of=now,
            source=conn.attestation_source or "connector.unobserved",
            scope=f"house:{house_id}/agent:{agent}",
            epoch={},
        ),
    }


async def observe_actuation_plane(
    *,
    cdp_url: str,
    target_id: str | None,
    port: CdpObservationPort,
    probe_js: str,
) -> dict[str, Any]:
    """CDP actuation plane — list tabs and optional target-scoped evaluate."""
    now = datetime.now(tz=UTC).isoformat()
    tabs = port.list_tabs(cdp_url=cdp_url)
    cdp_reachable: bool | None = True if tabs else None
    signed_in: bool | None = None
    stale = False
    scoped_url = ""
    conv_id: str | None = None

    if target_id:
        match = next((t for t in tabs if t.get("id") == target_id), None)
        if match is None:
            stale = True
        else:
            scoped_url = match.get("url") or ""
            conv_id = conversation_id_from_url(scoped_url)
            raw = await port.evaluate_on_target(
                cdp_url=cdp_url,
                target_id=target_id,
                expression=probe_js,
            )
            val = raw.get("signed_in")
            if isinstance(val, bool):
                signed_in = val
            elif val is None:
                signed_in = None
            else:
                signed_in = bool(val)

    return {
        "plane": "actuation",
        "cdp_url": cdp_url,
        "target_id": target_id,
        "cdp_reachable": status_basis(
            "cdp_reachable",
            cdp_reachable if tabs else None,
            as_of=now,
            source="cdp.json_list",
            scope=f"cdp:{cdp_url}",
            epoch={"tab_count": len(tabs)},
        ),
        "signed_in": status_basis(
            "signed_in",
            signed_in,
            as_of=now,
            source="cdp.evaluate" if target_id else "cdp.unscoped",
            scope=f"target:{target_id or 'none'}",
            epoch={"url": scoped_url, "conversation_id": conv_id},
            stale=stale,
        ),
        "tabs": [
            {
                "target_id": t.get("id"),
                "url": t.get("url"),
                "conversation_id": conversation_id_from_url(t.get("url") or ""),
                "title": t.get("title"),
            }
            for t in tabs
        ],
    }
