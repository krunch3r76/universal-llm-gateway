"""Cortex entity operations for evidence promotion (phase-d).

Idempotent entity ensure + duplicate-hash detection backing
``promote_document_to_evidence``. Pulled out of the tool handler so the
handler is orchestration-only and these helpers stay unit-testable
independently of the FastMCP wrapper.

Spec: cortex://notes/system/specs/document-ingestion-redesign.md
"""

from __future__ import annotations

from typing import Any

from universal_logging import get_logger

from ._cortex_relay import _cx
from ._promote_document_helpers import PromoteError, normalize_entity_content_hash

logger = get_logger(__name__)


def entity_get(entity_id: str) -> dict[str, Any] | None:
    """Fetch a cortex entity by id; return ``None`` on 404, raise on other errors."""
    result = _cx(
        "POST",
        "/dispatch",
        {"tool": "entity_get", "arguments": {"entity_id": entity_id}},
    )
    if result.get("error"):
        if result.get("status_code") == 404:
            return None
        raise RuntimeError(result["error"])
    return result


def find_document_with_content_hash(
    content_hash: str,
    *,
    exclude_entity_id: str,
) -> str | None:
    """Return another ``document:`` entity id with the same ``content_hash``.

    Used as the duplicate-evidence gate before ``entity_create``. The
    underlying cortex ``entities`` op caps at ``limit=200`` and has no
    cursor — beyond that boundary the gate degrades silently. See
    todo:F13 (paginate-content-hash-lookup) for the upstream change.
    """
    result = _cx(
        "POST",
        "/dispatch",
        {
            "tool": "entities",
            "arguments": {"type": "document", "limit": 200},
        },
    )
    if result.get("error"):
        logger.warning(
            "promote: entities list failed during duplicate check: %s",
            result["error"],
        )
        return None
    for row in result.get("entities", []):
        eid = row.get("id")
        if not eid or eid == exclude_entity_id:
            continue
        stored = normalize_entity_content_hash(row.get("content_hash"))
        if stored == content_hash:
            return str(eid)
    return None


def entity_create(
    *,
    entity_id: str,
    name: str,
    description: str,
    content_hash: str,
    source_uri: str | None,
    attributes: dict[str, Any] | None,
) -> tuple[bool, dict[str, Any] | None]:
    """Create entity; return ``(created, existing_entity_on_conflict)``.

    On a 409 conflict, fetches the existing entity for the caller to
    compare against. Other relay errors raise ``RuntimeError``.
    """
    payload: dict[str, Any] = {
        "id": entity_id,
        "type": "document",
        "name": name,
        "description": description,
        "content_hash": content_hash,
    }
    if source_uri is not None:
        payload["source_uri"] = source_uri
    if attributes:
        payload["attributes"] = attributes

    result = _cx(
        "POST",
        "/dispatch",
        {"tool": "entity_create", "arguments": payload},
    )
    if not result.get("error"):
        return True, None

    if result.get("status_code") != 409:
        raise RuntimeError(result["error"])

    existing = entity_get(entity_id)
    return False, existing


def ensure_entity(
    *,
    entity_id: str,
    name: str,
    description: str,
    content_hash: str,
    source_uri: str | None,
    attributes: dict[str, Any] | None,
) -> tuple[bool, dict[str, Any] | None]:
    """Idempotent entity ensure; raises ``PromoteError`` on hard conflicts.

    Sequence:
      1. ``entity_get`` — if it exists and content_hash matches, return
         ``(False, existing)``. If content_hash mismatches, raise
         ``entity_conflict``.
      2. ``find_document_with_content_hash`` — if a different entity already
         carries this hash, raise ``duplicate_evidence``.
      3. ``entity_create`` — on success ``(True, None)``. On 409, refetch
         and re-compare; either accept (idempotent) or raise
         ``entity_conflict``.
    """
    existing = entity_get(entity_id)
    if existing is not None:
        stored = normalize_entity_content_hash(existing.get("content_hash"))
        if stored != content_hash:
            raise PromoteError(
                "entity_conflict",
                f"Entity {entity_id!r} exists with content_hash {stored!r}, "
                f"but source SHA is {content_hash!r}.",
            )
        return False, existing

    duplicate = find_document_with_content_hash(
        content_hash,
        exclude_entity_id=entity_id,
    )
    if duplicate is not None:
        raise PromoteError(
            "duplicate_evidence",
            f"content_hash {content_hash!r} is already bound to "
            f"{duplicate!r}; reuse that entity or supersede.",
        )

    created, conflict_row = entity_create(
        entity_id=entity_id,
        name=name,
        description=description,
        content_hash=content_hash,
        source_uri=source_uri,
        attributes=attributes,
    )
    if created:
        return True, None

    if conflict_row is None:
        raise PromoteError(
            "entity_conflict",
            f"Entity {entity_id!r} already exists (409) but could not be loaded.",
        )
    stored = normalize_entity_content_hash(conflict_row.get("content_hash"))
    if stored != content_hash:
        raise PromoteError(
            "entity_conflict",
            f"Entity {entity_id!r} exists with content_hash {stored!r}, "
            f"but source SHA is {content_hash!r}.",
        )
    return False, conflict_row
