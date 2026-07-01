"""Entity ops — entities, entity_get, entity_create, entity_update."""

from __future__ import annotations

import sqlite3
from typing import Any

from fastapi import HTTPException
from universal_logging import get_logger

from ..card import CARD_INTENTS_DEFERRED as _CARD_INTENTS_DEFERRED
from ..card import CARD_TOP_K_DEFAULT as _CARD_TOP_K_DEFAULT
from ..db import WRITE_LOCK, cortex_conn
from ..entity_aliases import resolve_entity_reference
from ..entity_collision import attach_collision_warning, check_entity_collision
from ..entity_rekey import entity_merge_impl, entity_rekey_impl
from ..trait_vocabulary import (
    ADOPTION_VALUES,
    CONFIDENCE_BAND_VALUES,
    LIFECYCLE_VALUES,
)
from ..write_discipline_nudge import attach_write_discipline, build_entity_create_nudge
from ._shared import (
    _ENTITY_MUTABLE,
    _VALID_STATUS,
    _compute_content_hash,
    record,
    reject_trait_writes_at_create,
)

_TRAIT_VOCAB: dict[str, frozenset[str]] = {
    "confidence_band": CONFIDENCE_BAND_VALUES,
    "lifecycle": LIFECYCLE_VALUES,
    "adoption": ADOPTION_VALUES,
}


def _validate_trait_updates(updates: dict[str, object]) -> dict[str, Any] | None:
    """Reject out-of-vocab Option-C trait writes before they reach SQL.

    Returns an error dict for the dispatch surface, or None when every supplied
    trait value is valid (or absent).
    """
    for trait, vocab in _TRAIT_VOCAB.items():
        value = updates.get(trait)
        if value is not None and value not in vocab:
            return {
                "error": f"Invalid {trait} {value!r}. Must be one of: {sorted(vocab)}"
            }
    return None


def _impls() -> tuple:
    # Lazy import — entity_crud → workflow_state → dispatch_ops cycles at import.
    from ..card import get_entity_card
    from ..entity_crud import (
        create_entity_impl,
        list_entities_impl,
        update_entity_impl,
    )
    from ..entity_read import get_entity_impl

    return (
        create_entity_impl,
        get_entity_card,
        get_entity_impl,
        list_entities_impl,
        update_entity_impl,
    )


logger = get_logger("cortex-api.dispatch_ops.entities")

# agent_skill:markdown-navigation cutoff — size-aware body default.
_BODY_SIZE_AWARE_THRESHOLD = 5000


def _entity_get_body_response(
    conn: sqlite3.Connection,
    *,
    resolved_id: str,
    section: str | None,
    full_body: bool | None,
) -> dict[str, Any]:
    """Load source_uri markdown via the /skills/body file resolver.

    ``GET /skills/body`` applies skill-only SQL + lifecycle filters; this path
    reuses ``_resolve_skill_file`` for any entity carrying ``source_uri``.
    """
    from markdown_sections import SectionError, list_sections, read_section

    from ..db import query as db_query
    from ..routes._skill_index import slug_from_row
    from ..routes.boot._skill_trigger import _resolve_skill_file

    rows = db_query(
        conn,
        "SELECT id, name, source_uri FROM entities WHERE id = ?",
        (resolved_id,),
    )
    if not rows:
        return {"error": f"Entity not found: {resolved_id}"}
    row = rows[0]
    source_uri = row.get("source_uri")
    if not source_uri or not str(source_uri).strip():
        return {"error": "entity_has_no_source_uri", "entity_id": resolved_id}

    slug = slug_from_row(row)
    path = _resolve_skill_file(source_uri, slug)
    if path is None:
        return {
            "error": "entity_body_not_resolvable",
            "entity_id": resolved_id,
            "source_uri": source_uri,
        }
    try:
        body_text = path.read_text(encoding="utf-8")
    except OSError:
        return {
            "error": "entity_body_not_readable",
            "entity_id": resolved_id,
            "source_uri": source_uri,
        }

    base: dict[str, Any] = {
        "entity_id": resolved_id,
        "source_uri": source_uri,
    }

    if section is not None:
        try:
            section_text = read_section(body_text, section)
        except SectionError:
            available = [s["path"] for s in list_sections(body_text)]
            return {
                "error": "section_not_found",
                "section": section,
                "available": available,
                "entity_id": resolved_id,
            }
        return {
            **base,
            "render_mode": "full",
            "section": section,
            "body": section_text,
        }

    if full_body is True:
        return {**base, "render_mode": "full", "body": body_text}

    if full_body is False:
        return {
            **base,
            "render_mode": "manifest",
            "sections": list_sections(body_text),
        }

    if len(body_text) <= _BODY_SIZE_AWARE_THRESHOLD:
        return {**base, "render_mode": "full", "body": body_text}

    return {
        **base,
        "render_mode": "manifest",
        "sections": list_sections(body_text),
    }


def _resolve_read_entity_id(
    conn: sqlite3.Connection,
    entity_id: str,
    *,
    resolve_aliases: bool = True,
    raw_id: bool = False,
    label: str = "entity",
) -> str:
    try:
        resolved = resolve_entity_reference(
            conn,
            entity_id,
            resolve_aliases=resolve_aliases,
            raw_id=raw_id,
            label=label,
        )
    except HTTPException as exc:
        raise exc
    return resolved.entity_id


def _http_error_dict(exc: HTTPException) -> dict[str, Any]:
    return {"error": exc.detail, "status_code": exc.status_code}


def _op_entities(
    type: str | None = None,
    workflow_state: str | None = None,
    limit: int | None = None,
    query: str | None = None,
    for_agent: str | None = None,
    content_hash: str | None = None,
    fields: list[str] | None = None,
    include_non_active: bool = False,
    **_: object,
) -> dict[str, Any]:
    _, _, _, _list_entities_impl, _ = _impls()
    with cortex_conn() as conn:
        return _list_entities_impl(
            conn,
            entity_type=type,
            workflow_state=workflow_state,
            limit=limit or 50,
            query=query,
            for_agent=for_agent,
            content_hash=content_hash,
            fields=fields,
            include_non_active=include_non_active,
        )


def _op_entities_by_content_hash(
    content_hash: str | None = None,
    type: str | None = None,
    limit: int | None = None,
    **_: object,
) -> dict[str, Any]:
    """Dedicated content-hash lookup op. Requires content_hash; defaults limit=5."""
    if not content_hash:
        return {"error": "content_hash is required"}
    _, _, _, _list_entities_impl, _ = _impls()
    with cortex_conn() as conn:
        return _list_entities_impl(
            conn,
            entity_type=type,
            limit=limit or 5,
            content_hash=content_hash,
        )


def _op_entity_get(
    entity_id: str | None = None,
    include_edges: bool = False,
    edge_limit: int = 20,
    include_compaction_pointers: bool = False,
    intent: str = "card",
    include_superseded: bool = False,
    debug: bool = False,
    top_k: int = _CARD_TOP_K_DEFAULT,
    resolve_aliases: bool = True,
    raw_id: bool = False,
    section: str | None = None,
    full_body: bool | None = None,
    **_: object,
) -> dict[str, Any]:
    """Dispatch surface for entity_get (v2.4 §6.1).

    intent="full" — EntityDetail with active assertions + superseded breadcrumb.
    intent="full-historical" — all rows with full enrichment (audit path).
    intent="card" — Card v0 via projection-aware fetch (§6.3).
    intent="card-md" — comprehension-first markdown render (root-only).
    intent="body" — source_uri markdown (not the KG card). Params: ``section``
    (md_read one heading), ``full_body`` (``true``=whole body, ``false``=manifest).
    Default (no section, ``full_body`` unset): size-aware at 5000 chars —
    whole body when ``len <= 5000``, else section manifest. Response includes
    ``render_mode`` (``"full"`` | ``"manifest"``).
    intent in {"cluster","impact"} — reserved; rejected until later phases.
    """
    if not entity_id:
        return {"error": "entity_id is required"}
    if intent not in {
        "full",
        "full-historical",
        "card",
        "card-md",
        "body",
        "cluster",
        "impact",
    }:
        return {
            "error": f"Unknown intent {intent!r}. Supported: full, full-historical, card, "
            "card-md, body (cluster, impact reserved for later phases).",
        }
    if intent == "full-historical" and include_superseded:
        return {
            "error": (
                "Invalid combo: intent='full-historical' with include_superseded=true "
                "— use intent='full-historical' alone or intent='full' with "
                "include_superseded=true."
            ),
            "status_code": 400,
        }
    if include_superseded and intent in {
        "card",
        "card-md",
        "body",
        "cluster",
        "impact",
    }:
        return {
            "error": (
                f"Invalid combo: intent={intent!r} with include_superseded=true "
                "— include_superseded applies only to intent='full'."
            ),
            "status_code": 400,
        }
    if intent in _CARD_INTENTS_DEFERRED:
        return {
            "error": f"intent={intent!r} reserved but not implemented in Slice 1",
            "supported_intents": ["full", "card"],
            "reference": "cortex-v2.4 §6.1, §7.1, §7.3",
        }
    if intent in {"card", "card-md"} and (
        not isinstance(top_k, int) or top_k < 1 or top_k > 50
    ):
        return {"error": "top_k must be int in [1, 50]"}
    _, _get_entity_card_impl, _get_entity_impl, _, _ = _impls()
    with cortex_conn() as conn:
        try:
            canonical_id = _resolve_read_entity_id(
                conn,
                entity_id,
                resolve_aliases=resolve_aliases,
                raw_id=raw_id,
            )
        except HTTPException as exc:
            return _http_error_dict(exc)
        resolved_id = canonical_id if not raw_id else entity_id
        if intent == "body":
            return _entity_get_body_response(
                conn,
                resolved_id=resolved_id,
                section=section,
                full_body=full_body,
            )
        if intent == "card-md":
            from ..subgraph_template import render_root_card_markdown

            try:
                return render_root_card_markdown(
                    conn,
                    entity_id=resolved_id,
                    top_k=top_k,
                )
            except HTTPException as exc:
                return _http_error_dict(exc)
        if intent == "card":
            return _get_entity_card_impl(
                conn,
                entity_id=resolved_id,
                top_k=top_k,
                debug=debug,
            )
        return _get_entity_impl(
            conn,
            entity_id=canonical_id if not raw_id else entity_id,
            include_edges=include_edges,
            edge_limit=edge_limit,
            include_compaction_pointers=include_compaction_pointers,
            include_superseded=(
                intent == "full-historical" or (intent == "full" and include_superseded)
            ),
        )


def _op_entity_create(
    id: str | None = None,
    type: str | None = None,
    name: str | None = None,
    description: str | None = None,
    status: str | None = None,
    workflow_state: str | None = None,
    notes: str | None = None,
    aliases: list[str] | None = None,
    attributes: dict[str, Any] | None = None,
    source_uri: str | None = None,
    content_hash: str | None = None,
    **extra: object,
) -> dict[str, Any]:
    trait_error = reject_trait_writes_at_create(extra)
    if trait_error is not None:
        return trait_error
    required_fields = {"id": id, "type": type, "name": name}
    for field, val in required_fields.items():
        if not val:
            return {"error": f"{field} is required"}
    if status is not None and status not in _VALID_STATUS:
        return {
            "error": f"Invalid status {status!r}. "
            f"Must be one of: {sorted(_VALID_STATUS)}"
        }
    if source_uri is not None and content_hash is None:
        content_hash = _compute_content_hash(source_uri)
    payload: dict[str, Any] = {
        "id": id,
        "type": type,
        "name": name,
        **({} if description is None else {"description": description}),
        **({} if status is None else {"status": status}),
        **({} if workflow_state is None else {"workflow_state": workflow_state}),
        **({} if notes is None else {"notes": notes}),
        **({} if aliases is None else {"aliases": aliases}),
        **({} if attributes is None else {"attributes": attributes}),
        **({} if source_uri is None else {"source_uri": source_uri}),
        **({} if content_hash is None else {"content_hash": content_hash}),
    }
    _create_entity_impl, _, _get_entity_impl, _, _ = _impls()
    write_nudge = None
    try:
        with cortex_conn() as conn:
            write_nudge = build_entity_create_nudge(
                conn,
                entity_id=str(id),
                entity_type=str(type),
                name=str(name),
                description=description,
            )
    except Exception:  # noqa: BLE001 — advisory nudge must never block the create
        logger.warning(
            "build_entity_create_nudge failed for %s — proceeding without advisory",
            id,
            exc_info=True,
        )
        write_nudge = None
    try:
        with cortex_conn() as conn:
            result = _create_entity_impl(conn, payload)
    except sqlite3.IntegrityError:
        logger.warning("entity_create conflict for id=%s", id)
        try:
            with cortex_conn() as conn:
                existing = _get_entity_impl(conn, entity_id=str(id))
        except HTTPException as exc:
            # Conflict-lookup race — the IntegrityError says the entity
            # exists, so a 404 here is unexpected (concurrent delete? bad
            # state?). Log and proceed; the outer 409 still raises so the
            # caller sees the conflict.
            logger.warning(
                "entity_create conflict-lookup failed for %s: %s", id, exc.detail
            )
            existing = None
        raise HTTPException(
            status_code=409,
            detail={
                "detail": f"Entity already exists: {id}",
                "existing_entity": existing,
                "retryable": False,
            },
        )
    except sqlite3.OperationalError as exc:
        # Transient sqlite condition (locked, busy, IO error, disk full).
        # Distinct from IntegrityError above (caller error) — this is upstream
        # degradation that the agent should retry rather than treat as a fatal
        # malformed-input signal.
        logger.error(
            "entity_create transient cortex degradation for id=%s: %s", id, exc
        )
        raise HTTPException(
            status_code=503,
            detail={
                "detail": f"Cortex temporarily unavailable: {exc}",
                "retryable": True,
            },
        )
    if "error" not in result:
        logger.info("cortex entity_create: %s (%s)", id, type)
        record("mcp.cortex.entity.created", entity_id=id, entity_type=type)
        if write_nudge:
            attach_write_discipline(result, write_nudge)
        try:
            with cortex_conn() as conn:
                collision = check_entity_collision(
                    conn,
                    entity_id=str(id),
                    entity_type=str(type),
                    name=str(name),
                    description=description,
                )
            if collision is not None:
                attach_collision_warning(result, collision)
        except Exception:  # noqa: BLE001 — advisory warning must never block create
            logger.warning(
                "entity_create collision_warning failed for %s — proceeding",
                id,
                exc_info=True,
            )
    return result


def _op_entity_update(
    entity_id: str | None = None,
    resolve_aliases: bool = True,
    raw_id: bool = False,
    intent: str = "full",
    **kwargs: object,
) -> dict[str, Any]:
    if not entity_id:
        return {"error": "entity_id is required"}
    if intent not in {"full", "card", "cluster", "impact"}:
        return {
            "error": f"Unknown intent {intent!r}. Supported: full, card "
            "(cluster, impact reserved for later phases).",
        }
    if intent in _CARD_INTENTS_DEFERRED:
        return {
            "error": f"intent={intent!r} reserved but not implemented in Slice 1",
            "supported_intents": ["full", "card"],
            "reference": "cortex-v2.4 §6.1, §7.1, §7.3",
        }
    updates: dict[str, object] = {
        k: v for k, v in kwargs.items() if k in _ENTITY_MUTABLE
    }
    status_val = updates.get("status")
    if status_val is not None and status_val not in _VALID_STATUS:
        return {
            "error": f"Invalid status {status_val!r}. "
            f"Must be one of: {sorted(_VALID_STATUS)}"
        }
    trait_error = _validate_trait_updates(updates)
    if trait_error is not None:
        return trait_error
    if (
        "source_uri" in updates
        and updates["source_uri"] is not None
        and "content_hash" not in updates
    ):
        computed = _compute_content_hash(str(updates["source_uri"]))
        if computed:
            updates["content_hash"] = computed
    if not updates:
        return {"error": "No fields to update"}
    _, _, _, _, _update_entity_impl = _impls()
    with cortex_conn() as conn:
        try:
            canonical_id = _resolve_read_entity_id(
                conn,
                entity_id,
                resolve_aliases=resolve_aliases,
                raw_id=raw_id,
            )
        except HTTPException as exc:
            return _http_error_dict(exc)
        result = _update_entity_impl(
            conn,
            entity_id=canonical_id if not raw_id else entity_id,
            updates=updates,
            intent=intent,
        )
    if "error" not in result:
        logger.info("cortex entity_update: %s", entity_id)
    return result


def _op_entity_rekey(
    old_id: str | None = None,
    new_id: str | None = None,
    **_: object,
) -> dict[str, Any]:
    if not old_id:
        return {"error": "old_id is required"}
    if not new_id:
        return {"error": "new_id is required"}
    try:
        with WRITE_LOCK, cortex_conn() as conn:
            result = entity_rekey_impl(conn, old_id, new_id)
    except HTTPException as exc:
        return _http_error_dict(exc)
    logger.info("cortex entity_rekey: %s -> %s", old_id, result["new_id"])
    return result


def _op_entity_merge(
    source_id: str | None = None,
    target_id: str | None = None,
    **_: object,
) -> dict[str, Any]:
    if not source_id:
        return {"error": "source_id is required"}
    if not target_id:
        return {"error": "target_id is required"}
    try:
        with WRITE_LOCK, cortex_conn() as conn:
            result = entity_merge_impl(conn, source_id, target_id)
    except HTTPException as exc:
        return _http_error_dict(exc)
    logger.info("cortex entity_merge: %s -> %s", source_id, target_id)
    return result
