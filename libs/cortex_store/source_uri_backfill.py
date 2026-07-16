"""Transactional backfill for stranded attributes.source_uri rows."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass

from universal_logging import get_logger

from .db import decode_row, query
from .entity_crud import ENTITY_JSON_FIELDS, update_entity_impl
from .entity_source_uri import (
    is_blank_source_uri,
    nested_source_uri,
    strip_reserved_source_uri,
)

logger = get_logger("cortex-api.source_uri_backfill")

DEFAULT_EXPECTED_COUNT = 21

_STRANDED_SQL = """
SELECT id, attributes, source_uri
FROM entities
WHERE (source_uri IS NULL OR TRIM(source_uri) = '')
  AND json_extract(attributes, '$.source_uri') IS NOT NULL
  AND TRIM(json_extract(attributes, '$.source_uri')) != ''
ORDER BY id
"""

_RESIDUAL_SQL = """
SELECT COUNT(*) AS n
FROM entities
WHERE (source_uri IS NULL OR TRIM(source_uri) = '')
  AND json_extract(attributes, '$.source_uri') IS NOT NULL
  AND TRIM(json_extract(attributes, '$.source_uri')) != ''
"""


class SourceUriBackfillCountMismatchError(RuntimeError):
    """Stranded row count differs from the expected pre-image."""


class SourceUriBackfillVerificationError(RuntimeError):
    """Post-repair verification failed inside the transaction."""


@dataclass(frozen=True)
class SourceUriBackfillResult:
    stranded_count: int
    repaired_count: int
    residual_count: int
    applied: bool
    dry_run: bool


def select_stranded_rows(conn: sqlite3.Connection) -> list[dict[str, object]]:
    return query(conn, _STRANDED_SQL)


def residual_stranded_count(conn: sqlite3.Connection) -> int:
    rows = query(conn, _RESIDUAL_SQL)
    return int(rows[0]["n"]) if rows else 0


def run_source_uri_backfill(
    conn: sqlite3.Connection,
    *,
    dry_run: bool = True,
    expected_count: int = DEFAULT_EXPECTED_COUNT,
    post_commit_emits: list[Callable[[], None]] | None = None,
) -> SourceUriBackfillResult:
    """Repair stranded rows atomically through shared update_entity_impl."""
    emits = post_commit_emits if post_commit_emits is not None else []
    conn.execute("BEGIN IMMEDIATE")
    try:
        stranded = select_stranded_rows(conn)
        count = len(stranded)
        if count == 0:
            conn.rollback()
            return SourceUriBackfillResult(
                stranded_count=0,
                repaired_count=0,
                residual_count=0,
                applied=False,
                dry_run=dry_run,
            )
        if count != expected_count:
            conn.rollback()
            raise SourceUriBackfillCountMismatchError(
                f"expected {expected_count} stranded rows, found {count}"
            )

        expected_by_id: dict[str, str] = {}
        for row in stranded:
            attrs = row.get("attributes")
            if isinstance(attrs, str):
                attrs = json.loads(attrs)
            nested = nested_source_uri(attrs if isinstance(attrs, dict) else None)
            if nested is None:
                conn.rollback()
                raise SourceUriBackfillVerificationError(
                    f"row {row['id']!r} missing nested source_uri after select"
                )
            expected_by_id[str(row["id"])] = nested

        for entity_id in expected_by_id:
            update_entity_impl(
                conn,
                entity_id=entity_id,
                updates={"attributes": {}},
                commit=False,
                post_commit_emits=emits,
            )

        for entity_id, expected_uri in expected_by_id.items():
            rows = query(conn, "SELECT * FROM entities WHERE id = ?", (entity_id,))
            if not rows:
                conn.rollback()
                raise SourceUriBackfillVerificationError(
                    f"row {entity_id!r} missing after repair"
                )
            decoded = decode_row(rows[0], ENTITY_JSON_FIELDS)
            actual_uri = decoded.get("source_uri")
            if actual_uri != expected_uri:
                conn.rollback()
                raise SourceUriBackfillVerificationError(
                    f"row {entity_id!r}: canonical {actual_uri!r} != {expected_uri!r}"
                )
            attrs = decoded.get("attributes")
            if isinstance(attrs, dict) and nested_source_uri(attrs) is not None:
                conn.rollback()
                raise SourceUriBackfillVerificationError(
                    f"row {entity_id!r}: nested source_uri still present"
                )
            if is_blank_source_uri(actual_uri):
                conn.rollback()
                raise SourceUriBackfillVerificationError(
                    f"row {entity_id!r}: canonical source_uri still blank"
                )
            stripped = strip_reserved_source_uri(
                attrs if isinstance(attrs, dict) else None
            )
            if stripped != attrs:
                conn.rollback()
                raise SourceUriBackfillVerificationError(
                    f"row {entity_id!r}: attributes not stripped"
                )

        residual = residual_stranded_count(conn)
        if residual != 0:
            conn.rollback()
            raise SourceUriBackfillVerificationError(
                f"residual stranded count {residual}, expected 0"
            )

        if dry_run:
            conn.rollback()
            applied = False
        else:
            conn.commit()
            applied = True
            for emit in emits:
                emit()
    except Exception:
        conn.rollback()
        raise

    return SourceUriBackfillResult(
        stranded_count=count,
        repaired_count=count,
        residual_count=0,
        applied=applied,
        dry_run=dry_run,
    )
