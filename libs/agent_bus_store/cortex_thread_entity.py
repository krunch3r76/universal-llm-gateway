"""Auto-mint ``thread:{id}`` Cortex entities for ``role:root`` agent-bus houses.

R19: graph pointers ``agent-bus:N`` resolve to ``thread:N``; this module ensures
those entities exist when a root house is created or re-tagged. Failures are
observable (log + event) and never roll back bus thread creation.
"""

from __future__ import annotations

import json
import logging
from typing import Any

import httpx
from transport_utils import DEFAULT_CORTEX_URL, make_sync_client

from .continuity_hub_cards import hub_card_uri
from .continuity_watermark import hub_entity_id
from .thread_classification import ROLE_ROOT_TAG

log = logging.getLogger(__name__)

_CORTEX_TIMEOUT_S = 15.0
_MINT_SESSION = "agent-bus-thread-entity-mint"
_MINT_AGENT = "agent-bus"
_MINT_SOURCE = "workspaces://universal-llm-gateway/notes/system/specs/r19-bus-thread-entities.md"


def thread_entity_id(thread_id: str) -> str:
    """Canonical Cortex id for an agent-bus house thread."""
    return f"thread:{thread_id}"


def is_role_root_thread(tags: list[str] | None) -> bool:
    """True when the normalized tag set includes ``role:root``."""
    if not tags:
        return False
    normalized = {str(t).strip().lower() for t in tags if str(t).strip()}
    return ROLE_ROOT_TAG in normalized


def _cortex_dispatch(tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
    body = {"tool": tool, "arguments": json.dumps(arguments)}
    with make_sync_client(DEFAULT_CORTEX_URL, timeout=_CORTEX_TIMEOUT_S) as client:
        response = client.post("/dispatch", json=body)
    try:
        parsed = response.json()
    except ValueError:
        parsed = {"error": response.text[:500], "status_code": response.status_code}
    if not isinstance(parsed, dict):
        return {"error": f"non-object response: {type(parsed).__name__}"}
    if response.status_code >= 400 and "error" not in parsed:
        parsed = {
            **parsed,
            "error": f"HTTP {response.status_code}",
            "status_code": response.status_code,
        }
    return parsed


def _entity_exists(entity_id: str) -> bool:
    reply = _cortex_dispatch("entity_get", {"entity_id": entity_id, "intent": "card"})
    if reply.get("error"):
        status = reply.get("status_code")
        if status == 404:
            return False
        raise RuntimeError(str(reply.get("error")))
    return bool(reply.get("id") or reply.get("entity_id"))


def _entity_create_payload(thread_id: str, slug: str) -> dict[str, Any]:
    entity_id = thread_entity_id(thread_id)
    name = (slug or "").strip() or f"bus thread {thread_id}"
    return {
        "id": entity_id,
        "type": "thread",
        "name": name,
        "description": f"Agent-bus role:root house {thread_id} ({name})",
        "source_uri": hub_card_uri(thread_id),
        "duplicate_name_ok": True,
    }


def _create_entity(thread_id: str, slug: str) -> dict[str, Any]:
    payload = _entity_create_payload(thread_id, slug)
    reply = _cortex_dispatch("entity_create", payload)
    if reply.get("error"):
        status = reply.get("status_code")
        text = str(reply.get("error")).lower()
        if status == 409 or "already exists" in text or "conflict" in text:
            return {"created": False, "entity_id": payload["id"], "reason": "exists"}
        raise RuntimeError(str(reply.get("error")))
    return {
        "created": True,
        "entity_id": str(reply.get("id") or reply.get("entity_id") or payload["id"]),
    }


def _ensure_references_hub(thread_id: str) -> dict[str, Any]:
    """Create ``references`` edge thread→hub when the continuity document exists."""
    source_id = thread_entity_id(thread_id)
    target_id = hub_entity_id(thread_id)
    if not _entity_exists(target_id):
        return {"edge_created": False, "reason": "hub_missing"}
    reply = _cortex_dispatch(
        "relationship_create",
        {
            "source_id": source_id,
            "target_id": target_id,
            "type_id": "references",
            "role": "continuity_hub",
            "strength": 1.0,
            "session_id": _MINT_SESSION,
            "agent": _MINT_AGENT,
            "source_uri": _MINT_SOURCE,
        },
    )
    if reply.get("error"):
        text = str(reply.get("error")).lower()
        if reply.get("status_code") == 409 or "exists" in text:
            return {"edge_created": False, "reason": "edge_exists"}
        raise RuntimeError(str(reply.get("error")))
    was_new = reply.get("was_new", True)
    return {"edge_created": bool(was_new), "reason": "created" if was_new else "exists"}


def mint_thread_entity(
    thread_id: str,
    slug: str,
    *,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Mint ``thread:{id}`` and optional hub ``references`` edge (idempotent).

    Returns ``created`` / ``edge_created`` booleans plus ``entity_id``.
    When ``dry_run`` is true, reports what would happen without Cortex writes.
    """
    entity_id = thread_entity_id(thread_id)
    out: dict[str, Any] = {
        "thread_id": thread_id,
        "entity_id": entity_id,
        "created": False,
        "edge_created": False,
    }
    if dry_run:
        missing_entity = not _entity_exists(entity_id)
        out["created"] = missing_entity
        if missing_entity:
            out["would_create"] = _entity_create_payload(thread_id, slug)
        hub_id = hub_entity_id(thread_id)
        if _entity_exists(hub_id):
            out["would_edge"] = {"source_id": entity_id, "target_id": hub_id}
            out["edge_created"] = missing_entity
        return out

    if _entity_exists(entity_id):
        out["reason"] = "exists"
    else:
        created = _create_entity(thread_id, slug)
        out["created"] = bool(created.get("created"))
        out["entity_id"] = created.get("entity_id", entity_id)
        if created.get("reason") == "exists":
            out["reason"] = "exists"

    edge = _ensure_references_hub(thread_id)
    out["edge_created"] = bool(edge.get("edge_created"))
    out["edge_reason"] = edge.get("reason")
    return out


def list_role_root_threads_missing_entities() -> list[dict[str, str]]:
    """Return ``role:root`` bus threads lacking a ``thread:{id}`` Cortex entity."""
    from .db.threads import list_threads_v2

    missing: list[dict[str, str]] = []
    for row in list_threads_v2(tags=[ROLE_ROOT_TAG]):
        thread_id = str(row["id"])
        entity_id = thread_entity_id(thread_id)
        if not _entity_exists(entity_id):
            missing.append(
                {
                    "thread_id": thread_id,
                    "slug": str(row.get("slug") or ""),
                    "entity_id": entity_id,
                }
            )
    return missing


def ensure_thread_entity(thread_id: str, slug: str, tags: list[str] | None) -> None:
    """Best-effort auto-mint after bus writes; never raises into callers."""
    if not is_role_root_thread(tags):
        return
    entity_id = thread_entity_id(thread_id)
    try:
        result = mint_thread_entity(thread_id, slug)
    except httpx.RequestError as exc:
        _report_failure(thread_id, entity_id, f"cortex connection failed: {exc}", "mint")
        return
    except Exception as exc:
        _report_failure(thread_id, entity_id, str(exc), "mint")
        return
    if result.get("error"):
        _report_failure(thread_id, entity_id, str(result["error"]), "mint")


def _report_failure(
    thread_id: str, entity_id: str, error: str, phase: str
) -> None:
    log.warning(
        "thread entity mint failed thread=%s entity=%s phase=%s err=%s",
        thread_id,
        entity_id,
        phase,
        error,
    )
    from .events.thread_entity_mint import emit_thread_entity_mint_failed

    emit_thread_entity_mint_failed(
        thread=thread_id,
        entity_id=entity_id,
        error=error[:500],
        phase=phase,
    )


__all__ = [
    "ensure_thread_entity",
    "is_role_root_thread",
    "list_role_root_threads_missing_entities",
    "mint_thread_entity",
    "thread_entity_id",
]
