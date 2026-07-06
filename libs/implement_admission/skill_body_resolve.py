"""Table-first skill body resolution — shared SOT for Layer-C inline + GET /skills/body.

Historical ``_ulg`` suffix: repo-scoped ULG skills once used table keys like
``ulg-architecture_ulg`` before Lane-2 substantiation. Wave 0 + A1 inverted the
alias so bare ``ulg-architecture`` is canonical; explicit ``*_ulg`` callers still
resolve via ``CANONICAL_SLUG_ALIASES`` to the same ``source_uri``. Narrative:
cortex://notes/system/threads/4433-skeptic-reply-0d8766d9.md.
"""

from __future__ import annotations

import hashlib
from typing import Any

from implement_admission.skill_source_table import (
    SkillSourceResolveError,
    canonical_table_key,
    resolve_canonical_source_uri,
)

_ENTITY_LOOKUP_SQL = """
    SELECT id, name, source_uri, type, lifecycle
    FROM entities
    WHERE type IN ('agent_skill', 'rule', 'skill')
      AND (id = ? OR id = ? OR name = ?)
    ORDER BY
        CASE
            WHEN id = ? THEN 0
            WHEN id = ? THEN 1
            ELSE 2
        END
    LIMIT 1
"""


def content_digest(data: bytes) -> str:
    """SHA-256 digest prefix shared by route body resolution and ingest projection."""
    return f"sha256:{hashlib.sha256(data).hexdigest()[:16]}"


def body_digest(source_uri: str | None, slug: str) -> str | None:
    """Content digest of the resolved skill/rule body for the INDEX envelope."""
    from cortex_store.routes.boot._skill_trigger import _resolve_skill_file

    path = _resolve_skill_file(source_uri, slug)
    if path is None:
        return None
    try:
        data = path.read_bytes()
    except OSError:
        return None
    return content_digest(data)


def _lookup_entity_row(conn: Any, key: str, entity_id_hint: str) -> dict[str, Any] | None:
    from cortex_store.db import query as db_query

    agent_skill_id = f"agent_skill:{key}"
    rows = db_query(
        conn,
        _ENTITY_LOOKUP_SQL,
        (entity_id_hint, agent_skill_id, key, entity_id_hint, agent_skill_id),
    )
    return rows[0] if rows else None


def _stable_entity_id(row: dict[str, Any] | None, key: str, entity_id_hint: str) -> str:
    if row and row.get("id"):
        return str(row["id"])
    if ":" in entity_id_hint:
        return entity_id_hint
    return f"agent_skill:{key}"


def resolve_skill_body_from_table(
    slug_or_entity_id: str,
    *,
    include_non_active: bool = False,
    expected_digest: str | None = None,
    conn: Any | None = None,
) -> tuple[dict[str, Any] | None, str | None]:
    """Resolve body via D1 table ``source_uri`` with lifecycle + digest gates.

    Returns ``(payload, drop_reason)``. ``drop_reason`` is set when resolution
    fails (``body_missing``, ``digest_mismatch``). Lifecycle withholding still
    returns a payload dict with ``body: None`` and ``reason:
    inactive_lifecycle_withheld`` (HTTP 200 contract).
    """
    from cortex_store.confidence_field import DISCOVERABLE_SKILL_LIFECYCLE
    from cortex_store.db import cortex_conn
    from cortex_store.routes.boot._skill_trigger import _resolve_skill_file

    raw = slug_or_entity_id.strip()
    key = canonical_table_key(raw)
    entity_id_hint = raw if ":" in raw else f"agent_skill:{key}"

    try:
        source_uri = resolve_canonical_source_uri(key)
    except SkillSourceResolveError:
        return None, "body_missing"

    own_conn = conn is None
    if own_conn:
        conn = cortex_conn()
    try:
        row = _lookup_entity_row(conn, key, entity_id_hint)
    finally:
        if own_conn and conn is not None:
            conn.close()

    entity_id = _stable_entity_id(row, key, entity_id_hint)
    lifecycle = row.get("lifecycle") if row else None
    entity_type = row.get("type") if row else None
    is_skill = entity_type in ("agent_skill", "skill") if row else True
    discoverable = (not is_skill) or (lifecycle == DISCOVERABLE_SKILL_LIFECYCLE)

    if is_skill and row is not None and not discoverable and not include_non_active:
        return (
            {
                "id": entity_id,
                "source_uri": source_uri,
                "digest": body_digest(source_uri, key),
                "body": None,
                "lifecycle": lifecycle,
                "discoverable": False,
                "reason": "inactive_lifecycle_withheld",
            },
            None,
        )

    path = _resolve_skill_file(source_uri, key)
    if path is None:
        return None, "body_missing"
    try:
        body_text = path.read_text(encoding="utf-8")
    except OSError:
        return None, "body_missing"

    digest = body_digest(source_uri, key)
    if expected_digest and digest and expected_digest != digest:
        return (
            {
                "error": "digest_mismatch",
                "expected_digest": expected_digest,
                "digest": digest,
            },
            "digest_mismatch",
        )

    return (
        {
            "id": entity_id,
            "source_uri": source_uri,
            "digest": digest,
            "body": body_text,
            "lifecycle": lifecycle,
            "discoverable": discoverable,
        },
        None,
    )
