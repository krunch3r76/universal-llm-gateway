"""Combined status across connector and actuation planes."""

from __future__ import annotations

import asyncio
import json
import os
import sqlite3
from collections.abc import Callable
from typing import Any
from urllib.parse import urlparse

from web_tab.binding_snapshot import read_binding_head
from web_tab.cdp_port import CdpObservationPort, default_cdp_port
from web_tab.config import resolve_cdp_for_web_tab_options
from web_tab.connector_plane import (
    default_attestation_reader,
    observe_connector_plane_from_server,
)
from web_tab.conversation_id import conversation_id_from_url
from web_tab.observation import observe_actuation_plane
from web_tab.surface_adapter import probe_js_for_surface, unsupported_surface_error

_LIST_ROW_CAP = 50
_PASTE_RECEIPT_PREFIX = "paste_receipt:"


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


def _surface_from_url(url: str | None) -> str | None:
    if not isinstance(url, str) or not url.strip():
        return None
    host = urlparse(url.strip()).netloc.lower()
    if not host:
        return None
    if host == "chatgpt.com":
        return "chatgpt"
    if host == "claude.ai":
        return "claude"
    return host


def _paste_receipt_from_reason(reason: str | None) -> dict[str, Any] | None:
    text = str(reason or "")
    if not text.startswith(_PASTE_RECEIPT_PREFIX):
        return None
    try:
        parsed = json.loads(text[len(_PASTE_RECEIPT_PREFIX) :])
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def enumerate_house_agent_tab_index_pairs() -> list[tuple[str, str]]:
    """Distinct ``(house_id, agent)`` from wake ledger and subscription heads."""
    try:
        from agent_bus_store.db.connection import _db_path, connect
    except ImportError:
        return []
    if not os.path.isfile(_db_path()):
        return []
    pairs: set[tuple[str, str]] = set()
    try:
        with connect() as conn:
            for row in conn.execute(
                "SELECT DISTINCT thread_id, agent FROM endpoint_wake_ledger "
                "WHERE trim(coalesce(thread_id, '')) != '' "
                "AND trim(coalesce(agent, '')) != ''"
            ):
                pairs.add((str(row["thread_id"]).strip(), str(row["agent"]).strip()))
            sub_rows = conn.execute(
                """
                SELECT agent, thread_scope, home_house_id
                FROM agent_endpoint_subscriptions AS s
                WHERE s.id IN (
                    SELECT MAX(id) FROM agent_endpoint_subscriptions
                    GROUP BY agent, thread_scope
                )
                """
            ).fetchall()
            for row in sub_rows:
                agent = str(row["agent"] or "").strip()
                if not agent:
                    continue
                scope = str(row["thread_scope"] or "").strip()
                if scope and scope not in {"*", "main-wake"}:
                    pairs.add((scope, agent))
                home = row["home_house_id"]
                if isinstance(home, str) and home.strip():
                    pairs.add((home.strip(), agent))
    except (sqlite3.Error, OSError):
        return []
    return sorted(pairs)


def _read_agent_scope_binding(house_id: str, agent: str) -> dict[str, Any]:
    try:
        from agent_bus_store.db.endpoint_wake_ledger import (
            _cursor_glass_actuation,
            _orion_chat_actuation_from_row,
            resolve_subscription_for_thread,
        )
        from web_chat_relay.orion_wake import holder_conversation_url
    except ImportError:
        return {
            "binding_state": None,
            "url": None,
            "conversation_id": None,
            "binding_row_id": None,
            "cdp_url": None,
        }
    resolved = resolve_subscription_for_thread(agent, house_id)
    if resolved is None:
        return {
            "binding_state": "unbound",
            "url": None,
            "conversation_id": None,
            "binding_row_id": None,
            "cdp_url": None,
        }
    _route, row = resolved
    row_id = row.get("id")
    if str(row.get("state") or "") != "subscribed":
        return {
            "binding_state": "unbound",
            "url": None,
            "conversation_id": None,
            "binding_row_id": row_id,
            "cdp_url": None,
        }
    actuation = _orion_chat_actuation_from_row(row) or _cursor_glass_actuation(row)
    url: str | None = None
    conv: str | None = None
    cdp_url: str | None = None
    if isinstance(actuation, dict):
        raw_url = actuation.get("conversation_url")
        url = raw_url.strip() if isinstance(raw_url, str) and raw_url.strip() else None
        raw_cid = actuation.get("conversation_id")
        conv = raw_cid.strip() if isinstance(raw_cid, str) and raw_cid.strip() else None
        raw_cdp = actuation.get("cdp_url")
        cdp_url = raw_cdp.strip() if isinstance(raw_cdp, str) and raw_cdp.strip() else None
        if conv is None and url:
            conv = conversation_id_from_url(url)
    if not url:
        holder = holder_conversation_url(row)
        url = holder.strip() if isinstance(holder, str) and holder.strip() else None
        if url and conv is None:
            conv = conversation_id_from_url(url)
    return {
        "binding_state": "bound",
        "url": url,
        "conversation_id": conv,
        "binding_row_id": row_id,
        "cdp_url": cdp_url,
    }


def _wake_ledger_snapshots(
    house_id: str, agent: str
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    try:
        from agent_bus_store.db.connection import _db_path, connect
    except ImportError:
        return None, None
    if not os.path.isfile(_db_path()):
        return None, None
    try:
        with connect() as conn:
            delivered = conn.execute(
                "SELECT id, wake_id, state, reason, created_at FROM endpoint_wake_ledger "
                "WHERE thread_id=? AND agent=? AND state='delivered' "
                "ORDER BY id DESC LIMIT 1",
                (house_id, agent),
            ).fetchone()
            non_delivered = conn.execute(
                "SELECT id, wake_id, state, reason, created_at FROM endpoint_wake_ledger "
                "WHERE thread_id=? AND agent=? AND state!='delivered' "
                "ORDER BY id DESC LIMIT 1",
                (house_id, agent),
            ).fetchone()
    except (sqlite3.Error, OSError):
        return None, None
    return (
        dict(delivered) if delivered is not None else None,
        dict(non_delivered) if non_delivered is not None else None,
    )


def _delivered_wake_snapshot(row: dict[str, Any] | None) -> dict[str, Any] | None:
    if row is None:
        return None
    receipt = _paste_receipt_from_reason(str(row.get("reason") or ""))
    conv = None
    if isinstance(receipt, dict):
        raw = receipt.get("conversation_id")
        conv = raw.strip() if isinstance(raw, str) and raw.strip() else None
    return {
        "created_at": row.get("created_at"),
        "conversation_id": conv,
        "wake_id": row.get("wake_id"),
    }


def _non_delivered_wake_snapshot(row: dict[str, Any] | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return {
        "state": row.get("state"),
        "reason": row.get("reason"),
        "created_at": row.get("created_at"),
    }


def _row_passes_surface_filter(
    *,
    surface_filter: str,
    agent_scope_url: str | None,
    delivered_conversation_id: str | None,
) -> bool:
    if _surface_from_url(agent_scope_url) == surface_filter:
        return True
    if delivered_conversation_id and surface_filter == "chatgpt":
        return True
    return False


def _default_cdp_url() -> str:
    from web_chat_relay.orion_wake import DEFAULT_ORION_CDP_URL

    return DEFAULT_ORION_CDP_URL


def _tab_open_for_conversation(
    listed: dict[str, Any], conversation_id: str | None
) -> bool | None:
    reachable = listed.get("reachable")
    if reachable is False:
        return None
    if reachable is not True:
        return None
    if not conversation_id:
        return False
    for tab in listed.get("pages") or []:
        if not isinstance(tab, dict):
            continue
        cid = conversation_id_from_url(str(tab.get("url") or ""))
        if cid == conversation_id:
            return True
        if conversation_id in str(tab.get("url") or ""):
            return True
    return False


def _resolve_row_cdp_url(agent_scope: dict[str, Any], wake_payload: dict[str, Any] | None) -> str:
    scope_cdp = agent_scope.get("cdp_url")
    if isinstance(scope_cdp, str) and scope_cdp.strip():
        return scope_cdp.strip()
    current = (wake_payload or {}).get("current") or {}
    actuation = current.get("actuation") if isinstance(current, dict) else None
    if isinstance(actuation, dict):
        raw = actuation.get("cdp_url")
        if isinstance(raw, str) and raw.strip():
            return raw.strip()
    return _default_cdp_url()


async def build_status_list_report(
    *,
    agent_filter: str | None = None,
    surface_filter: str | None = None,
    port: CdpObservationPort | None = None,
    attestation_reader: Callable[[str, str], dict[str, Any]] | None = None,
    binding_resolver: Callable[[str, str], dict[str, Any] | None] | None = None,
    pair_enumerator: Callable[[], list[tuple[str, str]]] | None = None,
    row_cap: int = _LIST_ROW_CAP,
) -> dict[str, Any]:
    """Read-only index of houses with binding, wake, attestation, and tab-open hints."""
    reader = attestation_reader or default_attestation_reader
    resolver = binding_resolver or _resolve_binding
    enumerate_fn = pair_enumerator or enumerate_house_agent_tab_index_pairs
    cap = max(1, int(row_cap))

    pairs = enumerate_fn()
    if agent_filter:
        agent_norm = agent_filter.strip()
        pairs = [(h, a) for h, a in pairs if a == agent_norm]

    cdp = port or default_cdp_port()
    cdp_cache: dict[str, dict[str, Any]] = {}

    async def _listed_for(cdp_url: str) -> dict[str, Any]:
        if cdp_url not in cdp_cache:
            cdp_cache[cdp_url] = await asyncio.to_thread(
                cdp.list_tabs, cdp_url=cdp_url
            )
        return cdp_cache[cdp_url]

    rows: list[dict[str, Any]] = []
    for house_id, agent in pairs:
        agent_scope = _read_agent_scope_binding(house_id, agent)
        wake_payload = resolver(house_id, agent)
        wake_state = str((wake_payload or {}).get("state") or "unbound")
        delivered_row, non_delivered_row = _wake_ledger_snapshots(house_id, agent)
        delivered = _delivered_wake_snapshot(delivered_row)
        non_delivered = _non_delivered_wake_snapshot(non_delivered_row)

        if surface_filter:
            delivered_cid = (
                delivered.get("conversation_id") if isinstance(delivered, dict) else None
            )
            if not _row_passes_surface_filter(
                surface_filter=surface_filter,
                agent_scope_url=agent_scope.get("url"),
                delivered_conversation_id=delivered_cid,
            ):
                continue

        attestation = reader(house_id, agent)
        connector = observe_connector_plane_from_server(
            house_id=house_id,
            agent=agent,
            attestation=attestation,
        )
        last_turn = connector.get("last_attested_turn") or {}

        row_surface = _surface_from_url(agent_scope.get("url"))
        if row_surface is None and isinstance(delivered, dict):
            if delivered.get("conversation_id"):
                row_surface = "chatgpt"

        check_cid = agent_scope.get("conversation_id")
        if not check_cid and isinstance(delivered, dict):
            raw = delivered.get("conversation_id")
            check_cid = raw if isinstance(raw, str) else None
        if not check_cid:
            check_cid = _binding_conversation_id(wake_payload)

        cdp_url = _resolve_row_cdp_url(agent_scope, wake_payload)
        listed = await _listed_for(cdp_url)
        tab_open = _tab_open_for_conversation(listed, check_cid)

        rows.append(
            {
                "house_id": house_id,
                "agent": agent,
                "surface": row_surface,
                "binding_row_id": agent_scope.get("binding_row_id"),
                "agent_scope_binding_state": agent_scope.get("binding_state"),
                "agent_scope_url": agent_scope.get("url"),
                "agent_scope_conversation_id": agent_scope.get("conversation_id"),
                "wake_resolve_state": wake_state,
                "newest_delivered_wake": delivered,
                "newest_non_delivered_wake": non_delivered,
                "last_attested_turn": last_turn.get("last_attested_turn"),
                "last_attested_as_of": last_turn.get("as_of"),
                "tab_open": tab_open,
                "cdp_url": cdp_url,
            }
        )

    total = len(rows)
    truncated = total > cap
    if truncated:
        rows = rows[:cap]

    return {
        "ok": True,
        "mode": "list",
        "plane": "index",
        "rows": rows,
        "count": len(rows),
        "total": total,
        "truncated": truncated,
    }


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
        resolved_cdp = resolve_cdp_for_web_tab_options(surface=surface, cdp_url=cdp_url)
    except ValueError as exc:
        msg = str(exc)
        if msg in {"missing_cdp_url", "cdp_surface_mismatch"}:
            return {"ok": False, "error": {"code": msg}}
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
