"""Misc cortex ops: stats, surface forms, resolve, tags, ingest."""

from __future__ import annotations

import logging
from typing import Any

from ..routes.ingest import _ingest_document_impl
from ..routes.resolve import _resolve_cortex_uri_impl
from ..routes.stats import _get_stats_impl
from ..routes.surface_forms import _list_surface_forms_impl
from ..routes.tags import _assign_tag_impl, _list_tags_impl
from ._shared import record

logger = logging.getLogger("cortex-api.dispatch_ops.misc")


def _op_stats(**_: object) -> dict[str, Any]:
    return _get_stats_impl()


def _op_surface_forms(
    entity_id: str | None = None,
    mention: str | None = None,
    mention_type: str | None = None,
    limit: int | None = None,
    **_: object,
) -> dict[str, Any]:
    return _list_surface_forms_impl(
        entity_id=entity_id,
        mention=mention,
        limit=limit or 50,
    )


def _op_resolve(
    uri: str | None = None, tag: str | None = None, **_: object
) -> dict[str, Any]:
    if not uri:
        return {"error": "uri is required (e.g. cortex://decision/rag-phased-rollout)"}
    return _resolve_cortex_uri_impl(uri=uri, tag=tag)


def _op_tag_assign(
    tag_name: str | None = None,
    entity_id: str | None = None,
    assertion_id: int | None = None,
    agent: str | None = None,
    **_: object,
) -> dict[str, Any]:
    for field, val in [
        ("tag_name", tag_name),
        ("entity_id", entity_id),
        ("assertion_id", assertion_id),
        ("agent", agent),
    ]:
        if not val and val != 0:
            return {"error": f"{field} is required"}
    body = {
        "tag_name": tag_name,
        "entity_id": entity_id,
        "assertion_id": assertion_id,
        "assigned_by": agent,
    }
    result = _assign_tag_impl(body)
    if "error" not in result:
        logger.info(
            "cortex tag_assign: %s → assertion %s on %s",
            tag_name,
            assertion_id,
            entity_id,
        )
        record(
            "mcp.cortex.tag.assigned",
            tag_name=tag_name,
            entity_id=entity_id,
            assertion_id=assertion_id,
        )
    return result


def _op_tag_list(entity_id: str | None = None, **_: object) -> dict[str, Any]:
    if not entity_id:
        return {"error": "entity_id is required"}
    return _list_tags_impl(entity_id=entity_id)


def _op_tag_resolve(
    tag_name: str | None = None,
    entity_id: str | None = None,
    **_: object,
) -> dict[str, Any]:
    if not tag_name:
        return {"error": "tag_name is required"}
    if not entity_id:
        return {"error": "entity_id is required"}
    parts = entity_id.split(":", 1)
    if len(parts) != 2:
        return {
            "error": f"Invalid entity_id format: {entity_id!r} (expected TYPE:SLUG)"
        }
    uri = f"cortex://{parts[0]}/{parts[1]}"
    return _resolve_cortex_uri_impl(uri=uri, tag=tag_name)


def _op_ingest_document(
    source_uri: str | None = None,
    content: str | None = None,
    observer: str = "cursor",
    source_date: str | None = None,
    **_: object,
) -> dict[str, Any]:
    if not source_uri:
        return {"error": "source_uri is required"}
    if not content:
        return {"error": "content is required"}
    body: dict[str, Any] = {
        "source_uri": source_uri,
        "content": content,
        "observer": observer,
    }
    if source_date is not None:
        body["source_date"] = source_date
    result = _ingest_document_impl(body)
    if "error" not in result:
        chunk_count = result.get("chunk_count", 0)
        logger.info("cortex ingest_document: %s — %d chunks", source_uri, chunk_count)
        record(
            "mcp.cortex.ingest_document",
            source_uri=source_uri,
            chunk_count=chunk_count,
        )
    return result
