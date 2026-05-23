"""Misc cortex ops: stats, surface forms, resolve, tags, chunk resolver."""

from __future__ import annotations

from typing import Any

from universal_logging import get_logger

from ..rag_resolver import ChunkIdMismatchError, resolve_assertion_chunk
from ..routes.resolve import _resolve_cortex_uri_impl
from ..routes.stats import _get_stats_impl
from ..routes.surface_forms import _list_surface_forms_impl
from ..routes.tags import _assign_tag_impl, _list_tags_impl
from ._shared import record

logger = get_logger("cortex-api.dispatch_ops.misc")


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


def _op_resolve_assertion_chunk(
    assertion_id: int | None = None,
    **_: object,
) -> dict[str, Any]:
    """Resolve an assertion's chunk_id to RAG chunk text.

    Looks up the assertion's chunk_id and evidence_uris[0], normalizes the
    URI to a RAG source path, calls POST /chunks_by_index, and verifies
    round-trip fidelity. Raises ChunkIdMismatch (logged as error) if the
    returned chunk_id differs from the stored one.

    Returns: ChunkByIndexItem dict (chunk_id, source, chunk_index, text,
    metadata) or error dict.
    """
    if assertion_id is None:
        return {"error": "assertion_id is required (integer)"}
    try:
        chunk = resolve_assertion_chunk(int(assertion_id))
        record("mcp.cortex.resolve_assertion_chunk", assertion_id=assertion_id)
        return {
            "assertion_id": assertion_id,
            "chunk": chunk,
        }
    except ChunkIdMismatchError as exc:
        logger.error("resolve_assertion_chunk mismatch: %s", exc)
        return {
            "error": "chunk_id_mismatch",
            "detail": str(exc),
            "assertion_id": assertion_id,
        }
    except ValueError as exc:
        return {"error": str(exc), "assertion_id": assertion_id}
    except Exception as exc:
        logger.error("resolve_assertion_chunk failed: %s", exc)
        return {
            "error": f"RAG lookup failed: {exc}",
            "assertion_id": assertion_id,
        }
