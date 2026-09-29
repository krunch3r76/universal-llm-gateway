"""Inline runbook markdown on entity_get card responses (decision:induction-question-forces-the-read)."""

from __future__ import annotations

import os
import sqlite3
from typing import Any

# ~20KiB body leaves headroom for card JSON (assertions, manifest, terminal_facts)
# inside the MCP cursor_safe 32KiB response guard
# (services/mcp-server/response_size_guard.py MCP_CURSOR_RESPONSE_SIZE_LIMIT).
_DEFAULT_CAP = 20 * 1024
RUNBOOK_INLINE_BODY_MAX_BYTES = int(
    os.getenv("CORTEX_RUNBOOK_INLINE_BODY_MAX_BYTES", str(_DEFAULT_CAP))
)


def _truncate_utf8_bytes(text: str, max_bytes: int) -> str:
    encoded = text.encode("utf-8")
    if len(encoded) <= max_bytes:
        return text
    truncated = encoded[:max_bytes]
    while truncated:
        try:
            return truncated.decode("utf-8")
        except UnicodeDecodeError:
            truncated = truncated[:-1]
    return ""


def read_entity_source_markdown(
    conn: sqlite3.Connection,
    resolved_id: str,
) -> dict[str, Any]:
    """Load markdown for any entity with source_uri (shared with intent=body)."""
    from .db import query as db_query
    from .routes._skill_index import slug_from_row
    from .routes.boot._skill_trigger import _resolve_skill_file

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
    return {
        "body_text": body_text,
        "source_uri": str(source_uri),
    }


def build_inline_body_fields(body_text: str, source_uri: str) -> dict[str, Any]:
    """Return ``body`` plus ``body_inline`` metadata; truncate when over cap."""
    full_bytes = len(body_text.encode("utf-8"))
    cap = RUNBOOK_INLINE_BODY_MAX_BYTES
    if full_bytes <= cap:
        return {
            "body": body_text,
            "body_inline": {
                "truncated": False,
                "byte_count": full_bytes,
                "cap_bytes": cap,
            },
        }
    truncated_text = _truncate_utf8_bytes(body_text, cap)
    return {
        "body": truncated_text,
        "body_inline": {
            "truncated": True,
            "byte_count": full_bytes,
            "cap_bytes": cap,
            "read_full_at": (
                source_uri
                if source_uri.startswith("cortex://")
                else f"cortex://{source_uri.lstrip('/')}"
            ),
        },
    }


def maybe_attach_runbook_body(
    payload: dict[str, Any],
    conn: sqlite3.Connection,
    entity: dict[str, Any],
    *,
    include_body: bool | None,
) -> None:
    """Default-on for ``type=runbook`` when ``include_body`` is not explicitly false."""
    if str(entity.get("type")) != "runbook":
        return
    if include_body is False:
        return
    loaded = read_entity_source_markdown(conn, str(entity["id"]))
    if "error" in loaded:
        payload["body_read_error"] = loaded
        return
    inline = build_inline_body_fields(loaded["body_text"], loaded["source_uri"])
    payload.update(inline)
