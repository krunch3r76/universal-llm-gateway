"""Tests for restart-intent schema migration."""

from __future__ import annotations

import sqlite3
import threading

from scripts.model_manager.ui.controller.restart_intent_migrate import (
    _EXPIRED_MIGRATION_TEMP,
    _create_expired_migration_temp,
    _ensure_expired_status,
    apply_restart_intent_schema,
)
from scripts.model_manager.ui.controller.restart_intent_states import (
    STATUS_EXPIRED,
    STATUS_PENDING_DRAIN,
    STATUS_VERIFYING_ACTIVATION,
)


def _connect(path) -> sqlite3.Connection:
    conn = sqlite3.connect(path, timeout=30.0)
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def _seed_pre_expired_schema(conn: sqlite3.Connection) -> None:
    """Table shape immediately before the expired CHECK migration."""
    conn.executescript(
        """
        CREATE TABLE restart_intents (
            intent_id TEXT PRIMARY KEY,
            service TEXT NOT NULL,
            action TEXT NOT NULL DEFAULT 'restart',
            status TEXT NOT NULL CHECK (status IN (
                'pending_drain','drained_restarting','verifying_activation',
                'completed','activation_unverified',
                'failed','timeout','force_requested','cancelled')),
            drain_epoch INTEGER,
            worker_id TEXT,
            worker_started_at TEXT,
            deadline_at TEXT,
            last_seen_event_seq INTEGER NOT NULL DEFAULT 0,
            reason TEXT,
            kill_boundary_at TEXT,
            caller_agent TEXT,
            armed_at TEXT,
            expires_at TEXT,
            park_live INTEGER NOT NULL DEFAULT 0,
            park_summary TEXT,
            wait_for_boundary INTEGER NOT NULL DEFAULT 0,
            status_reason TEXT,
            status_changed_at TEXT,
            transitions TEXT NOT NULL DEFAULT '[]',
            arming_lanes TEXT,
            caller_thread_id TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        """
    )


def _allows_expired(conn: sqlite3.Connection) -> bool:
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='restart_intents'"
    ).fetchone()
    assert row and row[0]
    return f"'{STATUS_EXPIRED}'" in str(row[0])


def _row_count(conn: sqlite3.Connection) -> int:
    return int(
        conn.execute("SELECT COUNT(*) FROM restart_intents").fetchone()[0]
    )


def test_migration_allows_verifying_activation(tmp_path) -> None:
    """Schema migration preserves verifying_activation status rows across re-apply."""
    db = tmp_path / "restart-intents.db"
    conn = sqlite3.connect(db)
    conn.executescript(
        """
        CREATE TABLE restart_intents (
            intent_id TEXT PRIMARY KEY,
            service TEXT NOT NULL,
            action TEXT NOT NULL DEFAULT 'restart',
            status TEXT NOT NULL CHECK (status IN (
                'pending_drain','drained_restarting','completed',
                'failed','timeout','force_requested','cancelled')),
            drain_epoch INTEGER,
            worker_id TEXT,
            worker_started_at TEXT,
            deadline_at TEXT,
            last_seen_event_seq INTEGER NOT NULL DEFAULT 0,
            reason TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        """
    )
    apply_restart_intent_schema(conn)
    conn.execute(
        "INSERT INTO restart_intents (intent_id, service, status, created_at, updated_at) "
        "VALUES ('i1', 'git_integration_worker', ?, 't', 't')",
        (STATUS_VERIFYING_ACTIVATION,),
    )
    conn.commit()
    apply_restart_intent_schema(conn)
    row = conn.execute(
        "SELECT status FROM restart_intents WHERE intent_id='i1'"
    ).fetchone()
    assert row[0] == STATUS_VERIFYING_ACTIVATION


def test_expired_migration_leftover_temp_table(tmp_path) -> None:
    """Crash after CREATE temp must not block reopen (41247)."""
    db = tmp_path / "intents.db"
    conn = _connect(db)
    _seed_pre_expired_schema(conn)
    conn.execute(
        "INSERT INTO restart_intents "
        "(intent_id, service, status, created_at, updated_at) "
        "VALUES ('keep', 'git_integration_worker', ?, 't', 't')",
        (STATUS_PENDING_DRAIN,),
    )
    conn.commit()
    _create_expired_migration_temp(conn)
    conn.commit()
    assert conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
        (_EXPIRED_MIGRATION_TEMP,),
    ).fetchone()

    apply_restart_intent_schema(conn)
    conn.commit()
    assert _allows_expired(conn)
    assert _row_count(conn) == 1
    assert conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (_EXPIRED_MIGRATION_TEMP,),
    ).fetchone() is None


def test_expired_migration_finish_when_main_missing(tmp_path) -> None:
    """Half-migrated DB: temp holds rows, main dropped — finish without loss."""
    db = tmp_path / "intents.db"
    conn = _connect(db)
    _seed_pre_expired_schema(conn)
    conn.execute(
        "INSERT INTO restart_intents "
        "(intent_id, service, status, created_at, updated_at) "
        "VALUES ('survive', 'stargate', ?, 'c', 'u')",
        (STATUS_PENDING_DRAIN,),
    )
    conn.commit()
    _create_expired_migration_temp(conn)
    conn.execute(
        f"INSERT INTO {_EXPIRED_MIGRATION_TEMP} "
        "(intent_id, service, status, created_at, updated_at) "
        "SELECT intent_id, service, status, created_at, updated_at FROM restart_intents"
    )
    conn.execute("DROP TABLE restart_intents")
    conn.commit()

    _ensure_expired_status(conn)
    conn.commit()
    assert _allows_expired(conn)
    assert _row_count(conn) == 1
    row = conn.execute(
        "SELECT intent_id FROM restart_intents WHERE intent_id='survive'"
    ).fetchone()
    assert row is not None


def test_expired_migration_already_migrated_is_noop(tmp_path) -> None:
    db = tmp_path / "intents.db"
    conn = _connect(db)
    apply_restart_intent_schema(conn)
    conn.execute(
        "INSERT INTO restart_intents "
        "(intent_id, service, status, transitions, created_at, updated_at) "
        "VALUES ('x', 'mcp', ?, '[]', 't', 't')",
        (STATUS_EXPIRED,),
    )
    conn.commit()
    _create_expired_migration_temp(conn)
    conn.commit()

    _ensure_expired_status(conn)
    conn.commit()
    assert _allows_expired(conn)
    assert _row_count(conn) == 1
    assert conn.execute(
        "SELECT status FROM restart_intents WHERE intent_id='x'"
    ).fetchone()[0] == STATUS_EXPIRED


def test_expired_migration_concurrent_open(tmp_path) -> None:
    """Two openers racing migration preserve rows and widen CHECK once."""
    db = tmp_path / "intents.db"
    setup = _connect(db)
    _seed_pre_expired_schema(setup)
    services = ("git_integration_worker", "stargate", "mcp", "gateway", "rag")
    for i, service in enumerate(services):
        setup.execute(
            "INSERT INTO restart_intents "
            "(intent_id, service, status, created_at, updated_at) "
            "VALUES (?, ?, ?, 't', 't')",
            (f"id-{i}", service, STATUS_PENDING_DRAIN),
        )
    setup.commit()
    setup.close()

    barrier = threading.Barrier(2)
    errors: list[BaseException] = []

    def _worker() -> None:
        try:
            conn = _connect(db)
            barrier.wait(timeout=10)
            _ensure_expired_status(conn)
            conn.commit()
            conn.close()
        except BaseException as exc:
            errors.append(exc)

    threads = [threading.Thread(target=_worker) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
    assert not errors, errors

    verify = _connect(db)
    assert _allows_expired(verify)
    assert _row_count(verify) == 5
    verify.close()


def _seed_v1_legacy_schema(conn: sqlite3.Connection) -> None:
    """Oldest shape: no verifying_activation, no kill_boundary_at."""
    conn.executescript(
        """
        CREATE TABLE restart_intents (
            intent_id TEXT PRIMARY KEY,
            service TEXT NOT NULL,
            action TEXT NOT NULL DEFAULT 'restart',
            status TEXT NOT NULL CHECK (status IN (
                'pending_drain','drained_restarting','completed',
                'failed','timeout','force_requested','cancelled')),
            drain_epoch INTEGER,
            worker_id TEXT,
            worker_started_at TEXT,
            deadline_at TEXT,
            last_seen_event_seq INTEGER NOT NULL DEFAULT 0,
            reason TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        """
    )


def test_apply_schema_recovers_expired_temp_when_main_dropped(tmp_path) -> None:
    """41247 data-loss: crash after DROP main, before RENAME temp.

    Through ``apply_restart_intent_schema`` — ``_DDL`` must not mint an empty
    main that then causes the row-holding temp to be dropped.
    """
    db = tmp_path / "intents.db"
    conn = _connect(db)
    _seed_pre_expired_schema(conn)
    conn.execute(
        "INSERT INTO restart_intents "
        "(intent_id, service, status, created_at, updated_at) "
        "VALUES ('survive', 'stargate', ?, 'c', 'u')",
        (STATUS_PENDING_DRAIN,),
    )
    conn.commit()
    _create_expired_migration_temp(conn)
    conn.execute(
        f"INSERT INTO {_EXPIRED_MIGRATION_TEMP} "
        "(intent_id, service, status, created_at, updated_at) "
        "SELECT intent_id, service, status, created_at, updated_at FROM restart_intents"
    )
    conn.execute("DROP TABLE restart_intents")
    conn.commit()
    conn.close()

    conn = _connect(db)
    apply_restart_intent_schema(conn)
    conn.commit()
    assert _allows_expired(conn)
    assert _row_count(conn) == 1
    assert conn.execute(
        "SELECT intent_id FROM restart_intents WHERE intent_id='survive'"
    ).fetchone()
    assert conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (_EXPIRED_MIGRATION_TEMP,),
    ).fetchone() is None
    # Re-open is a stable no-op.
    apply_restart_intent_schema(conn)
    conn.commit()
    assert _row_count(conn) == 1


def test_apply_schema_recovers_v2_temp_when_main_dropped(tmp_path) -> None:
    """restart_intents_v2 crash after DROP main: rows restored, not dropped."""
    db = tmp_path / "intents.db"
    conn = _connect(db)
    _seed_v1_legacy_schema(conn)
    conn.execute(
        "INSERT INTO restart_intents (intent_id, service, status, created_at, updated_at) "
        "VALUES ('v2keep', 'git_integration_worker', 'pending_drain', 't', 't')"
    )
    conn.commit()
    conn.execute("ALTER TABLE restart_intents RENAME TO restart_intents_v2")
    conn.commit()
    conn.close()

    conn = _connect(db)
    apply_restart_intent_schema(conn)
    conn.commit()
    assert _allows_expired(conn)
    assert _row_count(conn) == 1
    assert conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='restart_intents_v2'"
    ).fetchone() is None


def test_apply_schema_leftover_v2_temp_with_main_does_not_crash_loop(tmp_path) -> None:
    """Crash after CREATE restart_intents_v2 (before DROP): reopen succeeds."""
    db = tmp_path / "intents.db"
    conn = _connect(db)
    _seed_v1_legacy_schema(conn)
    conn.execute(
        "INSERT INTO restart_intents (intent_id, service, status, created_at, updated_at) "
        "VALUES ('a', 'mcp', 'pending_drain', 't', 't')"
    )
    conn.execute("CREATE TABLE restart_intents_v2 (intent_id TEXT PRIMARY KEY)")
    conn.execute("INSERT INTO restart_intents_v2 VALUES ('partial')")
    conn.commit()

    apply_restart_intent_schema(conn)
    conn.commit()
    apply_restart_intent_schema(conn)
    conn.commit()
    assert _allows_expired(conn)
    assert _row_count(conn) == 1
    cols = {r[1] for r in conn.execute("PRAGMA table_info(restart_intents)")}
    assert "kill_boundary_at" in cols


def test_v2_migration_is_atomic_on_failure(tmp_path, monkeypatch) -> None:
    """A failure mid v2 rebuild rolls back: main intact, no temp left."""
    from scripts.model_manager.ui.controller import restart_intent_migrate as mig

    db = tmp_path / "intents.db"
    conn = _connect(db)
    _seed_v1_legacy_schema(conn)
    conn.execute(
        "INSERT INTO restart_intents (intent_id, service, status, created_at, updated_at) "
        "VALUES ('b', 'mcp', 'pending_drain', 't', 't')"
    )
    conn.commit()

    real_execute = conn.execute

    calls = {"n": 0}

    def _wrapped(sql, *a):
        if str(sql).strip().startswith("DROP TABLE restart_intents") and "IF" not in str(sql):
            calls["n"] += 1
            raise sqlite3.OperationalError("simulated crash")
        return real_execute(sql, *a)

    class _Proxy:
        def __getattr__(self, name):
            return getattr(conn, name)

        def execute(self, sql, *a):
            return _wrapped(sql, *a)

    try:
        mig._ensure_v2_shape(_Proxy())
    except sqlite3.OperationalError:
        pass
    assert calls["n"] == 1
    assert _row_count(conn) == 1
    assert conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='restart_intents_v2'"
    ).fetchone() is None
