"""Misc cortex ops: stats, surface forms, resolve, tags, chunk resolver."""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException
from universal_logging import get_logger

from ..db import cortex_conn
from ..entity_aliases import resolve_entity_reference
from ..event_publisher import cortex_pinned_deliverable_written
from ..routes.resolve import _resolve_cortex_uri_impl
from ..routes.stats import _get_stats_impl
from ..routes.surface_forms import _list_surface_forms_impl
from ..routes.tags import _assign_tag_impl, _list_tags_impl
from ._pinned_deliverable import write_pinned_deliverable_impl
from ._recon_sidecar import (
    content_sha256 as recon_content_sha256,
)
from ._recon_sidecar import (
    discards_advisory,
    recon_sidecar_uri,
    render_recon_sidecar_markdown,
    resolve_recon_target,
    write_recon_sidecar_file,
)
from ._shared import record
from ._thread_sidecar import (
    MAX_SIDECAR_CONTENT_CHARS,
    SidecarContentTooLargeError,
    SidecarWriteError,
    write_thread_sidecar_for_send,
)

logger = get_logger("cortex-api.dispatch_ops.misc")


def _op_recon_sidecar_write(
    label: str,
    theme: str,
    body: str,
    scopes: list[str] | None = None,
    queries: list[str] | None = None,
    sink_backend: str | None = None,
    **_: object,
) -> dict[str, Any]:
    resolved = resolve_recon_target(label, theme)
    if resolved is None:
        return {"error": "unsafe recon sidecar path"}
    label_slug, theme_slug, _path = resolved
    digest = recon_content_sha256(body)
    md = render_recon_sidecar_markdown(
        label=label,
        theme=theme,
        body=body,
        scopes=scopes,
        queries=queries,
        sink_backend=sink_backend or "cortex",
        sha256=digest,
    )
    try:
        path = write_recon_sidecar_file(label, theme, md)
    except ValueError as exc:
        return {"error": str(exc)}
    envelope: dict[str, Any] = {
        "uri": recon_sidecar_uri(label_slug, theme_slug),
        "path": path,
        "sha256": digest,
        "body_chars": len(body),
    }
    advisory = discards_advisory(body)
    if advisory:
        envelope["discards_advisory"] = advisory
    return envelope


def _op_thread_sidecar_write(
    thread: str,
    subject: str,
    content: str,
    from_agent: str | None = None,
    execution_id: str | None = None,
    oversized: bool = False,
    sidecar_slug: str | None = None,
    **_: object,
) -> dict[str, Any]:
    try:
        result = write_thread_sidecar_for_send(
            thread=thread,
            subject=subject,
            content=content,
            from_agent=from_agent or "dispatch",
            sidecar_slug=sidecar_slug,
            execution_id=execution_id,
            oversized=oversized,
        )
    except SidecarContentTooLargeError as exc:
        return {
            "error": "sidecar_content_too_large",
            "limit_chars": MAX_SIDECAR_CONTENT_CHARS,
            "body_chars": exc.body_chars,
        }
    except SidecarWriteError as exc:
        return {"error": f"sidecar_write_failed: {exc}"}
    return {
        "uri": result.uri,
        "path": result.path,
        "sha256": result.sha256,
        "body_chars": result.body_chars,
    }


def _op_pinned_deliverable_write(
    rel_path: str,
    content: str,
    write_if_absent: bool | None = None,
    dispatch_id: str | None = None,
    thread_id: str | None = None,
    **_: object,
) -> dict[str, Any]:
    if not rel_path:
        return {"error": "rel_path is required"}
    result = write_pinned_deliverable_impl(
        rel_path,
        content,
        write_if_absent=bool(write_if_absent),
    )
    if "error" not in result:
        cortex_pinned_deliverable_written(
            rel_path=rel_path,
            dispatch_id=dispatch_id,
            thread_id=thread_id,
            skipped=result.get("skipped"),
        )
    return result


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
        mention_type=mention_type,
        limit=limit or 50,
    )


def _op_resolve(
    uri: str | None = None, tag: str | None = None, **_: object
) -> dict[str, Any]:
    if not uri:
        return {"error": "uri is required (e.g. cortex://decision/rag-phased-rollout)"}
    if uri.startswith("transcript:"):
        from ..transcript_turn_resolve import resolve_transcript_turn_op

        return resolve_transcript_turn_op(uri)
    return _resolve_cortex_uri_impl(uri=uri, tag=tag)


def _resolve_tag_entity_id(
    entity_id: str,
    *,
    resolve_aliases: bool,
    raw_id: bool,
) -> str | dict[str, Any]:
    with cortex_conn() as conn:
        try:
            resolved = resolve_entity_reference(
                conn,
                entity_id,
                resolve_aliases=resolve_aliases,
                raw_id=raw_id,
            )
        except HTTPException as exc:
            return {"error": exc.detail, "status_code": exc.status_code}
    return resolved.entity_id if not raw_id else entity_id


def _op_tag_assign(
    tag_name: str | None = None,
    entity_id: str | None = None,
    assertion_id: int | None = None,
    agent: str | None = None,
    resolve_aliases: bool = True,
    raw_id: bool = False,
    **_: object,
) -> dict[str, Any]:
    for field, val in [
        ("tag_name", tag_name),
        ("entity_id", entity_id),
        ("assertion_id", assertion_id),
        ("agent", agent),
    ]:
        if not val and val != 0:
            if field == "assertion_id":
                return {
                    "error": (
                        "assertion_id is required — Kumiho tags pin a specific "
                        "assertion within the entity, not the entity itself "
                        "(UNIQUE(tag_name, entity_id) upserts by moving the "
                        "pointer). Locate or mint the target assertion first "
                        "(assert/observe/entity_get), then pass its id here."
                    )
                }
            return {"error": f"{field} is required"}
    assert entity_id is not None
    resolved_id = _resolve_tag_entity_id(
        entity_id, resolve_aliases=resolve_aliases, raw_id=raw_id
    )
    if isinstance(resolved_id, dict):
        return resolved_id
    body = {
        "tag_name": tag_name,
        "entity_id": resolved_id,
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


def _op_tag_list(
    entity_id: str | None = None,
    resolve_aliases: bool = True,
    raw_id: bool = False,
    **_: object,
) -> dict[str, Any]:
    if not entity_id:
        return {"error": "entity_id is required"}
    resolved_id = _resolve_tag_entity_id(
        entity_id, resolve_aliases=resolve_aliases, raw_id=raw_id
    )
    if isinstance(resolved_id, dict):
        return resolved_id
    return _list_tags_impl(entity_id=resolved_id)


def _op_tag_resolve(
    tag_name: str | None = None,
    entity_id: str | None = None,
    resolve_aliases: bool = True,
    raw_id: bool = False,
    **_: object,
) -> dict[str, Any]:
    if not tag_name:
        return {"error": "tag_name is required"}
    if not entity_id:
        return {"error": "entity_id is required"}
    resolved_id = _resolve_tag_entity_id(
        entity_id, resolve_aliases=resolve_aliases, raw_id=raw_id
    )
    if isinstance(resolved_id, dict):
        return resolved_id
    parts = resolved_id.split(":", 1)
    if len(parts) != 2:
        return {
            "error": f"Invalid entity_id format: {entity_id!r} (expected TYPE:SLUG)"
        }
    uri = f"cortex://{parts[0]}/{parts[1]}"
    return _resolve_cortex_uri_impl(uri=uri, tag=tag_name)
