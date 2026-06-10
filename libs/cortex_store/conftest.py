"""Shared pytest fixtures — head-schema DB via run_migrations template."""

from __future__ import annotations

from pathlib import Path

import pytest

from cortex_store._test_db_bootstrap import (
    _session_template_path,
    fresh_migrated_connection,
)


@pytest.fixture(scope="session")
def migrated_db_template(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return _session_template_path(tmp_path_factory)


@pytest.fixture()
def migrated_db_path(migrated_db_template: Path, tmp_path: Path) -> Path:
    db_path = tmp_path / "cortex_test.db"
    from cortex_store._test_db_bootstrap import copy_template_db

    copy_template_db(migrated_db_template, db_path)
    return db_path


@pytest.fixture()
def migrated_conn(migrated_db_template: Path, tmp_path: Path):
    conn = fresh_migrated_connection(tmp_path, migrated_db_template)
    yield conn
    conn.close()


@pytest.fixture()
def migrated_db_conn(migrated_conn):
    """Alias for tests that name the fixture ``migrated_db_conn``."""
    return migrated_conn


@pytest.fixture()
def session_env(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    migrated_db_template: Path,
) -> dict[str, Path]:
    """Isolated head-schema DB + files root for session-close integration tests."""
    from cortex_store import db
    from cortex_store._test_db_bootstrap import copy_template_db
    from cortex_store.dispatch_ops import ops_journals
    from cortex_store.routes import (
        session_close,
        session_close_helpers,
        session_close_persist,
        session_journals,
    )

    db_path = tmp_path / "cortex.db"
    files_root = tmp_path / "files"
    files_root.mkdir(parents=True)
    transcripts_root = tmp_path / "agent-transcripts"
    transcripts_root.mkdir()
    copy_template_db(migrated_db_template, db_path)
    monkeypatch.setattr(db, "_CORTEX_DB", db_path)
    monkeypatch.setattr(ops_journals, "_FILES_ROOT", files_root)
    monkeypatch.setattr(session_journals, "_FILES_ROOT", files_root)
    monkeypatch.setattr(session_close, "_FILES_ROOT", files_root)
    monkeypatch.setattr(session_close_persist, "_FILES_ROOT", files_root)
    monkeypatch.setattr(session_close_helpers, "_FILES_ROOT", files_root)
    monkeypatch.setenv("CURSOR_AGENT_TRANSCRIPTS_ROOT", str(transcripts_root))
    return {
        "db_path": db_path,
        "files_root": files_root,
        "transcripts_root": transcripts_root,
    }
