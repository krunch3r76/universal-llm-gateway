"""Resolve Glass ``agent_id`` for worker-scoped endpoint subscriptions at lane mint."""

from __future__ import annotations

import re

import httpx
from giw_endpoint import giw_api_url
from bus_watch.cursor_xvfb_subscription import parse_ide_holder_agent_id
from bus_watch.endpoint_wake_transport import (
    agent_id_matches_composer_suffix,
    composer_impresario_suffix,
)
from transport_utils import DEFAULT_AGENT_BUS_URL

_BARE_GLASS_AGENT_ID = re.compile(
    r"^[0-9a-f]{8}(?:-[0-9a-f]{4}){0,4}[0-9a-f]{0,12}$",
    re.I,
)

async def _cursor_agent_id_for_worker_suffix(
    client: httpx.AsyncClient,
    *,
    worker_thread_id: str,
    suffix: str,
    headers: dict[str, str],
) -> str | None:
    resp = await client.get(
        "/endpoint-subscriptions",
        params={"agent": "cursor", "thread_scope": worker_thread_id},
        headers=headers,
    )
    resp.raise_for_status()
    payload = resp.json()
    rows = payload.get("subscriptions") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        return None
    for row in rows:
        if not isinstance(row, dict) or row.get("state") != "subscribed":
            continue
        aid = str(row.get("agent_id") or "").strip()
        if aid and agent_id_matches_composer_suffix(aid, suffix):
            return aid
    return None


async def _holder_agent_id_for_lane(
    client: httpx.AsyncClient, *, worker_thread_id: str
) -> str | None:
    resp = await client.get(
        giw_api_url("/api/v1/git/admin/cse-holders"),
        params={"lane_thread_id": worker_thread_id},
    )
    resp.raise_for_status()
    payload = resp.json()
    holder = payload.get("holder") if isinstance(payload, dict) else None
    if not isinstance(holder, dict):
        return None
    hid = str(holder.get("holder_id") or "").strip()
    return parse_ide_holder_agent_id(hid)


async def resolve_worker_subscription_agent_id(
    client: httpx.AsyncClient,
    *,
    worker_thread_id: str,
    serving_to_agent: str | None,
    headers: dict[str, str] | None = None,
) -> tuple[str | None, str]:
    """Bind the serving Glass holder; never copy a parent coord ``agent_id``."""
    worker = str(worker_thread_id or "").strip()
    if not worker:
        return None, "unresolved:missing_worker_thread"
    hdrs = dict(headers or {})
    serving = str(serving_to_agent or "").strip()
    if serving and _BARE_GLASS_AGENT_ID.fullmatch(serving.lower()):
        return serving, f"serving_to_agent:{serving}"
    suffix = composer_impresario_suffix(serving)
    if suffix:
        aid = await _cursor_agent_id_for_worker_suffix(
            client,
            worker_thread_id=worker,
            suffix=suffix,
            headers=hdrs,
        )
        if aid:
            return aid, f"worker_subscription:{suffix}"
    try:
        holder_aid = await _holder_agent_id_for_lane(client, worker_thread_id=worker)
    except httpx.HTTPError:
        holder_aid = None
    if holder_aid:
        return holder_aid, f"lane_holder:ide:{holder_aid}"
    return None, "unresolved:no_serving_holder"


async def resolve_worker_subscription_agent_id_with_bus(
    *,
    worker_thread_id: str,
    serving_to_agent: str | None,
    bus_url: str = DEFAULT_AGENT_BUS_URL,
    bus_token: str = "",
) -> tuple[str | None, str]:
    headers = {"Authorization": f"Bearer {bus_token}"} if bus_token else {}
    async with httpx.AsyncClient(base_url=bus_url, timeout=10.0) as client:
        return await resolve_worker_subscription_agent_id(
            client,
            worker_thread_id=worker_thread_id,
            serving_to_agent=serving_to_agent,
            headers=headers,
        )
