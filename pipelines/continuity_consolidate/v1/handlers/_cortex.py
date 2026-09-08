"""cortex-api ``/dispatch`` access for the consolidate-continuity handlers.

One transport helper plus the few graph reads both steps share. Every call
returns a dict; failures surface as ``{"error": ...}`` and the caller decides
whether that is fatal (ingest: yes — no graph, no fold) or recordable
(apply: one failed write must not lose the others).
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from transport_utils import DEFAULT_CORTEX_URL, make_async_client

logger = logging.getLogger(__name__)

SEEDED_BY = "continuity-consolidate"
WATERMARK_PREFIX = "WATERMARK: consolidated_through="
MISSION_PREFIX = "MISSION: "
RESUME_PREFIX = "RESUME: "
CLAIM_PREFIXES = {
    "closed": "CLOSED: ",
    "decided": "DECIDED: ",
    "open": "OPEN: ",
    "superseded": "SUPERSEDED: ",
    "artifact": "ARTIFACT: ",
}
_WATERMARK_RE = re.compile(
    r"consolidated_through=(?P<thread>[0-9a-zA-Z_-]+)#(?P<turn>\d+)"
)

CORTEX_TIMEOUT_S = 20.0


def cortex_client():
    return make_async_client(DEFAULT_CORTEX_URL, timeout=CORTEX_TIMEOUT_S)


async def dispatch(client: Any, tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """POST /dispatch and normalise the reply into a dict with an optional ``error`` key."""
    try:
        resp = await client.post(
            "/dispatch", json={"tool": tool, "arguments": arguments}
        )
    except Exception as exc:  # noqa: BLE001 — transport failure is data here
        logger.warning("cortex dispatch %s transport failure: %s", tool, exc)
        return {"error": f"transport_error: {exc}"}
    if resp.status_code >= 400:
        try:
            body = resp.json()
        except Exception:  # noqa: BLE001
            body = None
        if isinstance(body, dict) and "error" in body:
            return body
        return {"error": f"http_{resp.status_code}: {resp.text[:300]}"}
    try:
        data = resp.json()
    except Exception as exc:  # noqa: BLE001
        return {"error": f"invalid_json_response: {exc}"}
    return data if isinstance(data, dict) else {"error": "non_object_response"}


def hub_entity_id(root_thread: str) -> str:
    """Convention shared with the continuity card: ``document:{root}-continuity``."""
    return f"document:{root_thread}-continuity"


async def resolve_hub(client: Any, root_thread: str) -> dict[str, Any] | None:
    """Return the hub entity dict, or None when the house has no hub yet.

    The hub is minted by the CHECKPOINT fold, not by this job — a house that
    has never checkpointed has nothing to consolidate into.
    """
    entity_id = hub_entity_id(root_thread)
    reply = await dispatch(
        client, "entity_get", {"entity_id": entity_id, "intent": "full"}
    )
    if "error" in reply:
        return None
    entity = reply.get("entity") if isinstance(reply.get("entity"), dict) else reply
    return entity if entity.get("id") else None


async def active_assertions(client: Any, entity_id: str) -> list[dict[str, Any]]:
    reply = await dispatch(
        client,
        "assertions",
        {"entity_id": entity_id, "superseded": False, "limit": 100, "intent": "full"},
    )
    rows = reply.get("assertions") or reply.get("items") or []
    return [r for r in rows if isinstance(r, dict)]


async def relationships(client: Any, entity_id: str) -> list[dict[str, Any]]:
    reply = await dispatch(
        client, "relationships", {"entity_id": entity_id, "limit": 100}
    )
    rows = reply.get("relationships") or reply.get("items") or []
    return [r for r in rows if isinstance(r, dict)]


def parse_watermark(rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Newest ``WATERMARK:`` assertion authored by this pipeline, decoded."""
    for row in sorted(rows, key=lambda r: int(r.get("id") or 0), reverse=True):
        if row.get("seeded_by") != SEEDED_BY:
            continue
        claim = str(row.get("claim") or "")
        match = _WATERMARK_RE.search(claim)
        if claim.startswith(WATERMARK_PREFIX) and match:
            return {
                "assertion_id": row.get("id"),
                "thread": match.group("thread"),
                "turn": int(match.group("turn")),
                "claim": claim,
            }
    return None


def compact(value: Any, limit: int) -> str:
    text = (
        value
        if isinstance(value, str)
        else json.dumps(value, default=str, ensure_ascii=False)
    )
    return text if len(text) <= limit else text[: limit - 1] + "…"
