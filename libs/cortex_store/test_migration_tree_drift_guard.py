"""Drift guards for the canonical cortex_store migration tree (G1 + G2)."""

from __future__ import annotations

import copy
import sqlite3
import tempfile
from pathlib import Path

from cortex_store._test_db_bootstrap import materialize_head_schema_template
from cortex_store.db import run_migrations
from cortex_store.schema_snapshot import (
    diff_schemas,
    dump_sqlite_schema,
    find_numbered_migrations_outside_canonical_tree,
    load_benign_allowlist,
    load_canonical_live_snapshot,
)

_REPO_ROOT = Path(__file__).resolve().parents[2]
_CANONICAL_MIGRATIONS = Path(__file__).resolve().parent / "migrations"


def test_g1_no_numbered_migrations_outside_canonical_tree() -> None:
    offenders = find_numbered_migrations_outside_canonical_tree(
        _REPO_ROOT,
        canonical_dir=_CANONICAL_MIGRATIONS,
    )
    assert offenders == [], (
        "numbered migration files outside libs/cortex_store/migrations: "
        + ", ".join(str(p.relative_to(_REPO_ROOT)) for p in offenders)
    )


def test_g2_empty_replay_matches_live_snapshot_modulo_allowlist() -> None:
    with tempfile.NamedTemporaryFile(suffix=".db") as handle:
        conn = sqlite3.connect(handle.name)
        conn.execute("PRAGMA foreign_keys=ON")
        run_migrations(conn)
        replay = dump_sqlite_schema(conn)
        conn.close()

    live = load_canonical_live_snapshot()
    allowlist = load_benign_allowlist()
    issues = diff_schemas(replay, live, allowlist)
    assert issues == [], "replay-vs-live schema drift:\n" + "\n".join(issues)


def test_g2_head_schema_template_matches_live_modulo_allowlist() -> None:
    """Session template path (used by conftest) must agree with the same guard."""
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "head.db"
        materialize_head_schema_template(db_path)
        conn = sqlite3.connect(db_path)
        replay = dump_sqlite_schema(conn)
        conn.close()

    issues = diff_schemas(
        replay, load_canonical_live_snapshot(), load_benign_allowlist()
    )
    assert issues == [], "head-template-vs-live schema drift:\n" + "\n".join(issues)


def test_g2_head_template_carries_version_stamp_and_registry_seed() -> None:
    """Template must stamp schema_version and seed relationship_types from snapshot."""
    snapshot = load_canonical_live_snapshot()
    expected_versions = snapshot.get("schema_versions", [])
    assert expected_versions, "fixture must carry schema_versions after refresh"

    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "head.db"
        materialize_head_schema_template(db_path)
        conn = sqlite3.connect(db_path)
        try:
            stamped = [
                r[0]
                for r in conn.execute(
                    "SELECT version FROM schema_version ORDER BY version"
                )
            ]
            assert stamped[: len(expected_versions)] == expected_versions
            assert all(v in stamped for v in expected_versions)

            derived_from = conn.execute(
                "SELECT 1 FROM relationship_types WHERE type='derived_from'"
            ).fetchone()
            assert derived_from is not None

            assert run_migrations(conn) == []
        finally:
            conn.close()


def test_g2_guard_trips_on_unlisted_divergence() -> None:
    """Prove G2 is not vacuously green — unlisted drift must fail."""
    with tempfile.NamedTemporaryFile(suffix=".db") as handle:
        conn = sqlite3.connect(handle.name)
        conn.execute("PRAGMA foreign_keys=ON")
        run_migrations(conn)
        replay = dump_sqlite_schema(conn)
        conn.close()

    perturbed = copy.deepcopy(replay)
    del perturbed["tables"]["entities"]

    issues = diff_schemas(
        perturbed, load_canonical_live_snapshot(), load_benign_allowlist()
    )
    assert issues == ["table missing from replay: entities"]
