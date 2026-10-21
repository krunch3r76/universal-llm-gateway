from __future__ import annotations

from cortex_store._absent_migration import (
    _MIGRATIONS_DIR,
    migration_filename_from_filenotfound,
)


def test_filenotfound_numbered_migration_py_returns_filename() -> None:
    missing = _MIGRATIONS_DIR / "055_session_journals_source_ref.py"
    exc = FileNotFoundError(2, "No such file or directory", str(missing))
    assert (
        migration_filename_from_filenotfound(exc)
        == "055_session_journals_source_ref.py"
    )


def test_filenotfound_outside_migrations_dir_returns_none() -> None:
    exc = FileNotFoundError(2, "No such file or directory", "/tmp/not-a-migration.py")
    assert migration_filename_from_filenotfound(exc) is None


def test_importerror_returns_none() -> None:
    assert migration_filename_from_filenotfound(ImportError("no module")) is None


def test_filenotfound_sql_in_migrations_dir_returns_none() -> None:
    missing = _MIGRATIONS_DIR / "001_initial_schema.sql"
    exc = FileNotFoundError(2, "No such file or directory", str(missing))
    assert migration_filename_from_filenotfound(exc) is None
