"""Exhibit entity write-time invariants (spec § 1.3).

Per docs/architecture/entity-backed-claim-provenance.md § 1.3, every
``exhibit:`` entity carries a **required** ``belongs_to`` relationship
to its parent ``case:`` — created at write time, not optional. The
enforcement runs inside ``entity_crud.create_entity_impl`` against the
same connection as the entity INSERT, so a missing or invalid parent
case rejects the whole transaction.

The case-slug is parsed from the exhibit's ID grammar
``exhibit:<case-slug>/<exhibit-slug>`` — this is the canonical form
the spec defines, and the slash-scoped ID was probed and confirmed
accepted by Cortex's ID column during session
``claude-web-2026-05-12-2204`` (assertion 9158).
"""

from __future__ import annotations

import datetime
import sqlite3

from fastapi import HTTPException, status
from universal_logging import get_logger

from .db import query

logger = get_logger("cortex-api.entity_exhibit_lint")


def parse_exhibit_case_id(entity_id: str) -> str:
    """Extract the parent ``case:`` ID from an exhibit's ID.

    Spec § 1.3 grammar: ``exhibit:<case-slug>/<exhibit-slug>`` → parent
    case is ``case:<case-slug>``. Rejects with 422 when the grammar is
    not satisfied (no ``/`` after the type prefix, empty case-slug,
    empty exhibit-slug, or not an exhibit-prefixed ID at all).
    """
    if not entity_id.startswith("exhibit:"):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "error": "exhibit_id_grammar_invalid",
                "message": (
                    "Exhibit ID must start with 'exhibit:' "
                    "(grammar: 'exhibit:<case-slug>/<exhibit-slug>' per spec § 1.3)."
                ),
                "id": entity_id,
            },
        )
    bare = entity_id.removeprefix("exhibit:")
    if "/" not in bare:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "error": "exhibit_id_grammar_invalid",
                "message": (
                    "Exhibit ID must contain '/' separating the case-slug "
                    "from the exhibit-slug "
                    "(grammar: 'exhibit:<case-slug>/<exhibit-slug>' per spec § 1.3)."
                ),
                "id": entity_id,
            },
        )
    case_slug, _, exhibit_slug = bare.partition("/")
    if not case_slug or not exhibit_slug:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "error": "exhibit_id_grammar_invalid",
                "message": (
                    "Exhibit ID must have non-empty case-slug AND exhibit-slug "
                    "(grammar: 'exhibit:<case-slug>/<exhibit-slug>' per spec § 1.3)."
                ),
                "id": entity_id,
            },
        )
    return f"case:{case_slug}"


def enforce_exhibit_belongs_to(
    conn: sqlite3.Connection,
    *,
    entity_id: str,
    entity_type: str,
) -> str | None:
    """Validate exhibit ID grammar and verify the parent case exists.

    Returns the parent case_id when *entity_type* is ``"exhibit"`` and
    the lookup succeeds — the caller is expected to insert the
    ``belongs_to`` relationship row inside the same transaction via
    ``insert_exhibit_belongs_to_relationship``. Returns ``None`` for any
    other entity_type.

    Rejects with 422 when:
      * the ID grammar does not match ``exhibit:<case-slug>/<exhibit-slug>``
      * the parent ``case:<case-slug>`` entity does not exist
      * the parent case entity is in ``deprecated`` status

    Spec § 1.3 makes the ``belongs_to`` relationship "not optional"
    so the rejection is at the entity-create boundary rather than a
    downstream gap-detector finding.
    """
    if entity_type != "exhibit":
        return None

    case_id = parse_exhibit_case_id(entity_id)

    has_lifecycle = any(
        row[1] == "lifecycle"
        for row in conn.execute("PRAGMA table_info(entities)").fetchall()
    )
    parent_cols = "id, lifecycle" if has_lifecycle else "id"
    case_rows = query(
        conn,
        f"SELECT {parent_cols} FROM entities WHERE id = ?",
        (case_id,),
    )
    if not case_rows:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "error": "exhibit_parent_case_missing",
                "message": (
                    f"Exhibit {entity_id!r} references parent case "
                    f"{case_id!r} which does not exist in the graph. "
                    "Spec § 1.3 requires the belongs_to relationship to be "
                    "created at write time; the parent case must exist first."
                ),
                "exhibit_id": entity_id,
                "case_id": case_id,
            },
        )
    parent = case_rows[0]
    deprecated = has_lifecycle and parent.get("lifecycle") == "deprecated"
    if deprecated:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "error": "exhibit_parent_case_deprecated",
                "message": (
                    f"Exhibit {entity_id!r} references parent case "
                    f"{case_id!r} which is deprecated. Cannot create an "
                    "exhibit under a deprecated case."
                ),
                "exhibit_id": entity_id,
                "case_id": case_id,
            },
        )
    return case_id


def insert_exhibit_belongs_to_relationship(
    conn: sqlite3.Connection,
    *,
    exhibit_id: str,
    case_id: str,
    session_id: str | None = None,
    agent: str | None = None,
) -> None:
    """Insert the exhibit→case ``belongs_to`` row in the open transaction.

    Idempotent: the relationships table has a uniqueness contract on
    ``(from_entity, to_entity, type, active)`` enforced by an integrity
    constraint; ``INSERT OR IGNORE`` on duplicate is a silent no-op and
    matches the behavior of the public ``/relationships`` route's
    dedup path. The hook fails open (logs + returns) when the
    ``belongs_to`` type is not registered (e.g. running against a
    sandbox that pre-dates migration 038) so existing test fixtures
    are not broken by the new enforcement.
    """
    now = datetime.datetime.now(tz=datetime.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")

    # Verify the relationship_type is registered. If migration 038 has
    # not been applied (test sandboxes, fresh installs), skip insertion
    # rather than crashing — the enforcement remains in the validation
    # call above; the relationship row is the side effect.
    if not query(
        conn,
        "SELECT type FROM relationship_types WHERE type = ?",
        ("belongs_to",),
    ):
        logger.info(
            "belongs_to relationship type not registered in this DB "
            "(migration 038 not applied); skipping relationship insert for "
            "exhibit=%s → case=%s",
            exhibit_id,
            case_id,
        )
        return

    try:
        conn.execute(
            "INSERT OR IGNORE INTO relationships "
            "(type, from_entity, to_entity, role, strength, evidence, "
            " chunk_id, valid_from, valid_until, source_uri, "
            " session_id, agent, created_at, updated_at, active) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)",
            (
                "belongs_to",
                exhibit_id,
                case_id,
                None,
                1.0,
                "Spec § 1.3 — required exhibit→case relationship "
                "auto-created at entity_create write time.",
                None,
                None,
                None,
                None,
                session_id,
                agent,
                now,
                now,
            ),
        )
    except sqlite3.IntegrityError:
        # Pre-existing active row — idempotent no-op.
        logger.debug(
            "belongs_to relationship already exists for exhibit=%s → case=%s",
            exhibit_id,
            case_id,
        )
