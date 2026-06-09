"""Best-effort closeout trigger when implement threads receive closeout replies."""

from __future__ import annotations

import json
import os
from typing import Any

from transport_utils import DEFAULT_STARGATE_URL, make_sync_client

_ENABLED = os.environ.get("AGENT_BUS_CLOSEOUT_TRIGGER", "1").strip().lower() not in (
    "0",
    "false",
    "no",
)


def maybe_trigger_closeout(
    *,
    thread: str,
    body: str | None,
    tags: list[str] | None = None,
) -> dict[str, Any] | None:
    if not _ENABLED or not body:
        return None
    effective_tags = tags or _thread_tags(thread)
    if "contract:implement" not in effective_tags:
        return None
    text = body.strip()
    if not text.startswith("{"):
        return None
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict) or payload.get("schema_version") != 1:
        return None
    if "source_ref" not in payload:
        return None
    stargate = os.environ.get("STARGATE_URL", DEFAULT_STARGATE_URL)
    with make_sync_client(stargate, timeout=120.0) as client:
        resp = client.post(
            "/api/v1/implement/closeout",
            json={"closeout": payload, "source_ref": payload.get("source_ref")},
        )
        if resp.status_code >= 400:
            return {"ok": False, "error": resp.text[:300]}
        try:
            return resp.json()
        except json.JSONDecodeError:
            return {"ok": False, "error": "non-json stargate response"}


def _thread_tags(thread_id: str) -> list[str]:
    from agent_bus_store.db import get_thread

    row = get_thread(thread_id)
    if not row:
        return []
    raw = row.get("tags")
    return list(raw) if isinstance(raw, list) else []
