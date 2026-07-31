"""Unit tests for migration 052 (drop entities.status).

Applies migrations 050 + 052 on an in-memory DB and confirms:

  * ``status`` column is removed; trait columns remain.
  * ``idx_v3_entities_status`` and ``current_entities`` view are updated.
  * ``type_confidence_fields`` rows storing ``status`` are renamed.
  * Re-running 052 is a no-op.
"""

from __future__ import annotations

import importlib.util
import sqlite3
from pathlib import Path

import pytest

_MIG_DIR = Path(__file__).parent / "migrations"


def _load_migration(stem: str):
    path = _MIG_DIR / f"{stem}.py"
    spec = importlib.util.spec_from_file_location(stem, path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


migration_050 = _load_migration("050_status_trait_normalization_phase0")
migration_052 = _load_migration("052_drop_entities_status_column")


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}


@pytest.fixture()
def conn() -> sqlite3.Connection:
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.executescript(
        """
        CREATE TABLE entities (
            id TEXT PRIMARY KEY,
            type TEXT NOT NULL,
            name TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'unsubstantiated',
            created_at TEXT
        );
        CREATE TABLE type_confidence_fields (
            entity_type TEXT PRIMARY KEY,
            confidence_field TEXT NOT NULL
        );
        INSERT INTO entities (id, type, name, status)
        VALUES ('person:test', 'person', 'Test', 'confirmed');
        INSERT INTO type_confidence_fields (entity_type, confidence_field)
        VALUES ('legacy', 'status');
        CREATE INDEX idx_v3_entities_status ON entities(status);
        CREATE VIEW current_entities AS
        SELECT * FROM entities WHERE status NOT IN ('merged', 'deprecated');
        """
    )
    return c


def test_drops_status_after_phase0(conn: sqlite3.Connection) -> None:
    migration_050.migrate(conn)
    migration_052.migrate(conn)
    cols = _columns(conn, "entities")
    assert "status" not in cols
    assert {"lifecycle", "confidence_band", "confidence_score", "adoption"} <= cols
    row = conn.execute(
        "SELECT lifecycle, confidence_band FROM entities WHERE id = 'person:test'"
    ).fetchone()
    assert row["lifecycle"] is None
    assert row["confidence_band"] is None


def test_registry_and_index_cleaned(conn: sqlite3.Connection) -> None:
    migration_050.migrate(conn)
    migration_052.migrate(conn)
    field = conn.execute(
        "SELECT confidence_field FROM type_confidence_fields WHERE entity_type = 'legacy'"
    ).fetchone()[0]
    assert field == "confidence_band"
    idx = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='index' AND name='idx_v3_entities_status'"
    ).fetchone()
    assert idx is None
    view_sql = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='view' AND name='current_entities'"
    ).fetchone()[0]
    assert "status" not in str(view_sql).lower()


def test_idempotent_rerun(conn: sqlite3.Connection) -> None:
    migration_050.migrate(conn)
    migration_052.migrate(conn)
    first = _columns(conn, "entities")
    migration_052.migrate(conn)
    assert _columns(conn, "entities") == first


def test_preflight_requires_trait_columns() -> None:
    c = sqlite3.connect(":memory:")
    c.executescript(
        "CREATE TABLE entities (id TEXT PRIMARY KEY, type TEXT, name TEXT, status TEXT);"
    )
    with pytest.raises(RuntimeError, match="trait columns"):
        migration_052.migrate(c)


def test_require_entities_status_column_aborts_after_052(
    conn: sqlite3.Connection,
) -> None:
    from cortex_store.status_trait_backfill import (
        _ENTITIES_STATUS_DROPPED_MSG,
        require_entities_status_column,
    )

    migration_050.migrate(conn)
    migration_052.migrate(conn)
    with pytest.raises(SystemExit) as exc:
        require_entities_status_column(conn)
    assert exc.value.code == 2
    assert "migration 052" in _ENTITIES_STATUS_DROPPED_MSG


# --- 1172-E: trait-completeness cert on :memory: without status column ------


def _post052_conn() -> sqlite3.Connection:
    """In-memory DB in post-052 state: trait columns present, status absent."""
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.executescript(
        """
        CREATE TABLE entities (
            id TEXT PRIMARY KEY,
            type TEXT NOT NULL,
            name TEXT NOT NULL,
            confidence_band TEXT,
            confidence_score REAL,
            lifecycle TEXT,
            adoption TEXT,
            updated_at TEXT
        );
        CREATE TABLE type_confidence_fields (
            entity_type TEXT PRIMARY KEY,
            confidence_field TEXT NOT NULL
        );
        """
    )
    return c


def test_cert_pass_on_post_052_memory_db() -> None:
    """Cert PASS: post-052 DB with all traits populated, no status column."""
    import os
    import sys

    sys.path.insert(
        0, os.path.join(os.path.dirname(__file__), "..", "..", "scripts", "cortex")
    )
    from trait_fallback_equivalence_cert import (
        run_cert,  # type: ignore[import]  # noqa: PLC0415
    )

    c = _post052_conn()
    c.executescript(
        """
        INSERT INTO entities (id, type, name, confidence_band, lifecycle, adoption)
        VALUES
          ('todo:a', 'todo', 'A', 'confirmed', 'active', NULL),
          ('decision:b', 'decision', 'B', 'provisional', 'active', 'proposed');
        """
    )
    passed, report = run_cert(c, ":memory:")
    assert passed, f"Expected PASS but got FAIL:\n{report}"
    assert "PASS" in report


def test_cert_fail_if_status_column_still_present() -> None:
    """Cert FAIL: status column still present → migration 052 not applied."""
    import os
    import sys

    sys.path.insert(
        0, os.path.join(os.path.dirname(__file__), "..", "..", "scripts", "cortex")
    )
    from trait_fallback_equivalence_cert import (
        run_cert,  # type: ignore[import]  # noqa: PLC0415
    )

    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.executescript(
        """
        CREATE TABLE entities (
            id TEXT PRIMARY KEY,
            type TEXT NOT NULL,
            name TEXT NOT NULL,
            status TEXT,
            confidence_band TEXT,
            lifecycle TEXT,
            adoption TEXT
        );
        INSERT INTO entities (id, type, name, status, confidence_band, lifecycle)
        VALUES ('todo:x', 'todo', 'X', 'active', 'confirmed', 'active');
        """
    )
    passed, report = run_cert(c, ":memory:")
    assert not passed
    assert "FAIL" in report


def test_cert_fail_on_null_trait_columns() -> None:
    """Cert FAIL: entities with NULL confidence_band or lifecycle."""
    import os
    import sys

    sys.path.insert(
        0, os.path.join(os.path.dirname(__file__), "..", "..", "scripts", "cortex")
    )
    from trait_fallback_equivalence_cert import (
        run_cert,  # type: ignore[import]  # noqa: PLC0415
    )

    c = _post052_conn()
    c.executescript(
        """
        INSERT INTO entities (id, type, name, confidence_band, lifecycle, adoption)
        VALUES ('todo:null', 'todo', 'N', NULL, NULL, NULL);
        """
    )
    passed, report = run_cert(c, ":memory:")
    assert not passed
    assert "FAIL" in report


# --- 1172-E: is_external_source_uri unit tests (D3) -------------------------


def test_is_external_http_uri() -> None:
    from cortex_store.confidence_policy import is_external_source_uri

    assert is_external_source_uri("https://example.com/page") is True
    assert is_external_source_uri("http://leginfo.legislature.ca.gov/doc") is True
    assert is_external_source_uri("ftp://files.example.org/data") is True


def test_is_external_gov_uri() -> None:
    from cortex_store.confidence_policy import is_external_source_uri

    assert is_external_source_uri("https://lacounty.gov/filing") is True


def test_internal_scheme_not_external() -> None:
    from cortex_store.confidence_policy import is_external_source_uri

    assert is_external_source_uri("cortex:notes/system/threads/1172.md") is False
    assert is_external_source_uri("agent-bus:1172") is False
    assert is_external_source_uri("email-bridge:msg-20260101") is False
    assert is_external_source_uri("internal:session-abc") is False


def test_bare_path_not_external() -> None:
    from cortex_store.confidence_policy import is_external_source_uri

    assert is_external_source_uri("notes/system/threads/some-file.md") is False
    assert is_external_source_uri("tmp/prompts/foo.md") is False
    assert is_external_source_uri("") is False


def test_non_internal_non_network_scheme_is_external() -> None:
    """A scheme not in the internal list and no netloc is still external."""
    from cortex_store.confidence_policy import is_external_source_uri

    # e.g. urn: or doi: would count as external (not in _INTERNAL_URI_SCHEMES)
    assert is_external_source_uri("urn:isbn:0-486-27557-4") is True
