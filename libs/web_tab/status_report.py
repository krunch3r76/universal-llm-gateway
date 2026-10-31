"""Combined status across connector and actuation planes."""

from __future__ import annotations

from typing import Any

from web_tab.binding_snapshot import read_binding_head
from web_tab.cdp_port import CdpObservationPort, default_cdp_port
from web_tab.observation import ConnectorPlaneInput, observe_actuation_plane, observe_connector_plane
from web_tab.probe import DEFAULT_ORION_CDP_URL
from web_tab.surface_adapter import probe_js_for_surface


async def build_status_report(
    *,
    house_id: str,
    agent: str,
    cdp_url: str = DEFAULT_ORION_CDP_URL,
    target_id: str | None = None,
    surface: str = "chatgpt",
    connector: ConnectorPlaneInput | None = None,
    binding_resolve: dict[str, Any] | None = None,
    port: CdpObservationPort | None = None,
) -> dict[str, Any]:
    """Status for connector + actuation + read-only binding head."""
    conn = connector or ConnectorPlaneInput(
        connector_alive=None,
        last_attested_turn=None,
        attestation_source=None,
    )
    cdp = port or default_cdp_port()
    js = probe_js_for_surface(surface)
    return {
        "ok": True,
        "house_id": house_id,
        "agent": agent,
        "surface": surface,
        "connector": observe_connector_plane(house_id=house_id, agent=agent, conn=conn),
        "actuation": await observe_actuation_plane(
            cdp_url=cdp_url,
            target_id=target_id,
            port=cdp,
            probe_js=js,
        ),
        "binding": read_binding_head(
            house_id=house_id,
            agent=agent,
            resolve_payload=binding_resolve,
        ),
        "_next": "probe → bind handshake (write slice); eyes slice stops at observation.",
    }
