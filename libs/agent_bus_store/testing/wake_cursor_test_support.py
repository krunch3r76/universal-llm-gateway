"""Pytest isolation for agent-bus DB paths, wake-cursor test resets, and event emit."""

from __future__ import annotations

import os
import pwd
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import pytest

_FIXTURE_HINT = (
    "Set AGENT_BUS_DB_PATH to a pytest tmp file (see "
    "libs/agent_bus_store/tests/conftest.py or libs/bus_watch/conftest.py)."
)

_PRODUCTION_DB_CANDIDATES = (Path("/data/messages.db"),)

_pytest_tmp_roots: list[Path] = []


def set_pytest_tmp_roots(*roots: Path) -> None:
    """Register tmp directories permitted for ``rewind_wake_cursor_for_test``."""
    global _pytest_tmp_roots
    resolved = [r.resolve() for r in roots if r is not None]
    _pytest_tmp_roots = resolved


def _production_agent_bus_db_paths() -> set[Path]:
    home = Path(pwd.getpwuid(os.getuid()).pw_dir)
    paths = {(home / ".agent-bus" / "messages.db").resolve()}
    for candidate in _PRODUCTION_DB_CANDIDATES:
        try:
            paths.add(candidate.resolve())
        except OSError:
            paths.add(candidate)
    return paths


def refuse_live_agent_bus_db_under_pytest(db_path: str | Path) -> None:
    """Raise when pytest would open the configured live agent-bus SQLite file."""
    if not os.environ.get("PYTEST_CURRENT_TEST"):
        return
    try:
        resolved = Path(os.path.expanduser(str(db_path))).resolve()
    except OSError:
        return
    if resolved in _production_agent_bus_db_paths():
        raise RuntimeError(
            f"refusing to open the live agent-bus DB under pytest: {resolved}. "
            f"{_FIXTURE_HINT}"
        )


def _assert_test_safe_main_db(conn: sqlite3.Connection) -> None:
    rows = conn.execute("PRAGMA database_list").fetchall()
    main_file: str | None = None
    for seq, name, file_name in rows:
        if name == "main":
            main_file = file_name
            break
    if not main_file or main_file == ":memory:":
        return
    try:
        resolved = Path(main_file).resolve()
    except OSError as exc:
        raise RuntimeError(f"wake cursor test helper: cannot resolve DB path: {exc}") from exc
    for root in _pytest_tmp_roots:
        if resolved == root or resolved.is_relative_to(root):
            return
    pytest_tmp = os.environ.get("PYTEST_DEBUG_TEMPROOT", "")
    if pytest_tmp:
        try:
            base = Path(pytest_tmp).resolve()
            if resolved.is_relative_to(base):
                return
        except OSError:
            pass
    parts = resolved.parts
    if "pytest" in parts and any(p.startswith("pytest-") for p in parts):
        return
    raise RuntimeError(
        "rewind_wake_cursor_for_test refuses DB outside pytest tmp roots: "
        f"{resolved}. {_FIXTURE_HINT}"
    )


def rewind_wake_cursor_for_test(
    conn: sqlite3.Connection,
    consumer: str,
    to: int = 0,
    *,
    updated_at: str | None = None,
) -> None:
    """Reset wake cursor high water via DELETE+INSERT (bypasses no-rewind UPDATE trigger)."""
    _assert_test_safe_main_db(conn)
    stamp = updated_at or datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    conn.execute("DELETE FROM endpoint_wake_cursor WHERE consumer=?", (consumer,))
    conn.execute(
        "INSERT INTO endpoint_wake_cursor (consumer, high_water_turn_id, updated_at) "
        "VALUES (?, ?, ?)",
        (consumer, int(to), stamp),
    )


def isolate_agent_bus_db_for_pytest(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    db_name: str = "agent-bus-messages.db",
) -> Path:
    """Point agent-bus DB env at a fresh file under ``tmp_path`` and bootstrap schema."""
    from agent_bus_store.db.connection import init_db

    db_path = tmp_path / db_name
    monkeypatch.setenv("AGENT_BUS_DB_PATH", str(db_path))
    monkeypatch.delenv("AGENT_BUS_ENDPOINT_WAKE_DISPATCHER", raising=False)
    set_pytest_tmp_roots(tmp_path)
    refuse_live_agent_bus_db_under_pytest(db_path)
    if db_path.exists():
        db_path.unlink()
    init_db()
    return db_path


def isolate_endpoint_wake_event_emit_for_pytest(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Keep endpoint_wake pass-duration observations off the live event ingest socket."""
    noop_sock = tmp_path / "pytest-events-ingest.sock"
    monkeypatch.setenv("MCP_EVENTS_ENABLED", "0")
    monkeypatch.setenv("EVENTS_INGEST_SOCK", str(noop_sock))
    try:
        import mcp_events

        monkeypatch.setattr(mcp_events, "_publisher", None)
        monkeypatch.setattr(
            mcp_events,
            "record",
            lambda *_a, **_k: None,
        )
    except ImportError:
        pass
    try:
        from bus_watch.events import register_uds_publisher

        register_uds_publisher(lambda *_a, **_k: None)
    except ImportError:
        pass
