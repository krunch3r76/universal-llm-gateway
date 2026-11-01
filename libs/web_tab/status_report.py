"""Combined status across connector and actuation planes."""

from __future__ import annotations

from typing import Any

from web_tab.binding_snapshot import read_binding_head
from web_tab.cdp_port import CdpObservationPort, default_cdp_port
from web_tab.config import resolve_cdp_url
from web_tab.connector_plane import (
    latest_bus_attestation,
    observe_connector_plane_from_server,
)
from web_tab.conversation_id import conversation_id_from_url
from web_tab.observation import observe_actuation_plane
from web_tab.surface_adapter import probe_js_for_surface, unsupported_surface_error


def _binding_conversation_id(resolve_payload: dict[str, Any] | None) -> str | None:
    if not resolve_payload:
        return None
    current = resolve_payload.get("current") or {}
    if not isinstance(current, dict):
        return None
    actuation = current.get("actuation") or {}
    if not isinstance(actuation, dict):
        return None
    cid = actuation.get("conversation_id")
    if isinstance(cid, str) and cid.strip():
        return cid.strip()
    return conversation_id_from_url(str(actuation.get("conversation_url") or ""))


def _resolve_binding(house_id: str, agent: str) -> dict[str, Any] | None:
    try:
        from agent_bus_store.db.endpoint_wake_ledger import (
            resolve_wake_delivery_binding,
        )
    except ImportError:
        return None
    return resolve_wake_delivery_binding(house_id=house_id, agent=agent)


async def build_status_report(
    *,
    house_id: str,
    agent: str,
    cdp_url: str | None = None,
    target_id: str | None = None,
    surface: str = "chatgpt",
    caller_identity: str | None = None,
    oauth_client_id: str | None = None,
    port: CdpObservationPort | None = None,
) -> dict[str, Any]:
    """Status for connector + actuation + read-only binding head."""
    try:
        resolved_cdp = resolve_cdp_url(cdp_url)
    except ValueError:
        return {"ok": False, "error": {"code": "missing_cdp_url"}}

    try:
        js = probe_js_for_surface(surface)
    except ValueError as exc:
        if str(exc).startswith("unsupported_surface:"):
            return unsupported_surface_error(surface)
        raise

    turn_no, attested = latest_bus_attestation(house_id)
    binding_payload = _resolve_binding(house_id, agent)
    binding_conv = _binding_conversation_id(binding_payload)

    cdp = port or default_cdp_port()
    return {
        "ok": True,
        "house_id": house_id,
        "agent": agent,
        "surface": surface,
        "connector": observe_connector_plane_from_server(
            house_id=house_id,
            agent=agent,
            caller_identity=caller_identity,
            oauth_client_id=oauth_client_id,
            last_attested_turn=turn_no,
            attestation_source=attested,
        ),
        "actuation": await observe_actuation_plane(
            cdp_url=resolved_cdp,
            target_id=target_id,
            port=cdp,
            probe_js=js,
            house_id=house_id,
            binding_conversation_id=binding_conv,
        ),
        "binding": read_binding_head(
            house_id=house_id,
            agent=agent,
            resolve_payload=binding_payload,
        ),
        "_next": "probe → bind handshake (write slice); eyes slice stops at observation.",
    }
