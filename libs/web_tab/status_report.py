"""Combined status across connector and actuation planes."""

from __future__ import annotations

import os
import sqlite3
from collections.abc import Callable
from typing import Any

from web_tab.binding_snapshot import read_binding_head
from web_tab.cdp_port import CdpObservationPort, default_cdp_port
from web_tab.config import resolve_cdp_url
from web_tab.connector_plane import (
    default_attestation_reader,
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
    """Read the wake binding. A missing bus file stays missing (no create)."""
    try:
        from agent_bus_store.db.connection import _db_path
        from agent_bus_store.db.endpoint_wake_ledger import (
            resolve_wake_delivery_binding,
        )
    except ImportError:
        return None
    if not os.path.isfile(_db_path()):
        return None
    try:
        return resolve_wake_delivery_binding(house_id=house_id, agent=agent)
    except (sqlite3.Error, OSError):
        return None


async def build_status_report(
    *,
    house_id: str,
    agent: str,
    cdp_url: str | None = None,
    target_id: str | None = None,
    surface: str = "chatgpt",
    port: CdpObservationPort | None = None,
    attestation_reader: Callable[[str, str], dict[str, Any]] | None = None,
    binding_resolver: Callable[[str, str], dict[str, Any] | None] | None = None,
    authenticated_principal: str | None = None,
) -> dict[str, Any]:
    """Status for connector + actuation + read-only binding head.

    Pipeline option identity is not a parameter. Pass *authenticated_principal*
    only from a caller that already authenticated it outside the option bag.
    *attestation_reader* and *binding_resolver* default to the server stores
    and must not create a missing database.
    """
    try:
        resolved_cdp = resolve_cdp_url(cdp_url)
    except ValueError as exc:
        msg = str(exc)
        if msg == "missing_cdp_url":
            return {"ok": False, "error": {"code": "missing_cdp_url"}}
        if msg.startswith("invalid_cdp_url"):
            return {"ok": False, "error": {"code": "invalid_cdp_url", "message": msg}}
        raise

    try:
        js = probe_js_for_surface(surface)
    except ValueError as exc:
        if str(exc).startswith("unsupported_surface:"):
            return unsupported_surface_error(surface)
        raise

    reader = attestation_reader or default_attestation_reader
    resolver = binding_resolver or _resolve_binding
    attestation = reader(house_id, agent)
    binding_payload = resolver(house_id, agent)
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
            attestation=attestation,
            authenticated_principal=authenticated_principal,
        ),
        "actuation": await observe_actuation_plane(
            cdp_url=resolved_cdp,
            target_id=target_id,
            port=cdp,
            probe_js=js,
            house_id=house_id,
            binding_conversation_id=binding_conv,
            surface=surface,
        ),
        "binding": read_binding_head(
            house_id=house_id,
            agent=agent,
            resolve_payload=binding_payload,
        ),
        "_next": "probe → bind handshake (write slice); eyes slice stops at observation.",
    }
