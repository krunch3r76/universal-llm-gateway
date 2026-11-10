"""Pytest isolation for agent-bus DB paths, wake-cursor test resets, and event emit."""

from __future__ import annotations

import os
import pwd
import sqlite3
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import pytest

_FIXTURE_HINT = (
    "Set AGENT_BUS_DB_PATH to a pytest tmp file (see "
    "libs/agent_bus_store/tests/conftest.py or libs/bus_watch/conftest.py)."
)

_PRODUCTION_DB_CANDIDATES = (
    Path("/data/messages.db"),
    Path("/home/io/.agent-bus/messages.db"),
)

_pytest_tmp_roots: list[Path] = []


def set_pytest_tmp_roots(*roots: Path) -> None:
    """Register tmp directories permitted for ``rewind_wake_cursor_for_test``."""
    global _pytest_tmp_roots
    resolved = [r.resolve() for r in roots if r is not None]
    _pytest_tmp_roots = resolved


def _pytest_basetemp_root() -> Path | None:
    debug = os.environ.get("PYTEST_DEBUG_TEMPROOT", "").strip()
    if debug:
        try:
            return Path(debug).resolve()
        except OSError:
            pass
    user = pwd.getpwuid(os.getuid()).pw_name
    base = Path(tempfile.gettempdir()) / f"pytest-of-{user}"
    try:
        return base.resolve()
    except OSError:
        return base


def _is_under_allowed_tmp(path: Path) -> bool:
    resolved = path.resolve()
    for root in _pytest_tmp_roots:
        if resolved == root or resolved.is_relative_to(root):
            return True
    basetemp = _pytest_basetemp_root()
    if basetemp is not None:
        try:
            if resolved == basetemp or resolved.is_relative_to(basetemp):
                return True
        except OSError:
            pass
    return False


def _production_agent_bus_db_paths() -> set[Path]:
    home = Path(pwd.getpwuid(os.getuid()).pw_dir)
    paths = {(home / ".agent-bus" / "messages.db").resolve()}
    for candidate in _PRODUCTION_DB_CANDIDATES:
        try:
            paths.add(candidate.resolve())
        except OSError:
            paths.add(candidate)
    return paths


def _matches_live_bus_db(resolved: Path, live: Path) -> bool:
    if resolved == live:
        return True
    if not live.is_file():
        return False
    try:
        return os.path.samefile(resolved, live)
    except OSError:
        return False


def refuse_live_agent_bus_db_under_pytest(db_path: str | Path) -> None:
    """Raise when pytest would open the configured live agent-bus SQLite file."""
    if not os.environ.get("PYTEST_CURRENT_TEST"):
        return
    try:
        resolved = Path(os.path.expanduser(str(db_path))).resolve()
    except OSError:
        return
    for live in _production_agent_bus_db_paths():
        if _matches_live_bus_db(resolved, live):
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
        raise RuntimeError(
            f"wake cursor test helper: cannot resolve DB path: {exc}"
        ) from exc
    if _is_under_allowed_tmp(resolved):
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

    def _noop_record(*_a: object, **_k: object) -> None:
        return None

    try:
        import bus_watch.events as bw_events

        from agent_bus_store.events import publisher as abs_publisher

        monkeypatch.setattr(bw_events, "record", _noop_record)
        monkeypatch.setattr(bw_events, "_uds_publisher", None)
        monkeypatch.setattr(abs_publisher, "_publisher", None)
    except ImportError:
        pass


def _import_mcp_events_module():
    try:
        import mcp_events

        return mcp_events
    except ImportError:
        import sys
        from pathlib import Path

        repo = Path(__file__).resolve().parents[3]
        mcp_dir = repo / "services" / "mcp-server"
        if not mcp_dir.is_dir():
            raise
        sys.path.insert(0, str(mcp_dir))
        import mcp_events

        return mcp_events


def track_uds_put_nowait(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Install a spy on ``mcp_events._UDSPublisher.put_nowait``; return the enqueue log."""
    enqueued: list[str] = []
    try:
        mcp_events = _import_mcp_events_module()
    except ImportError:
        return enqueued

    original = mcp_events._UDSPublisher.put_nowait

    def _spy(self: Any, line: str) -> None:
        enqueued.append(line)
        return original(self, line)

    monkeypatch.setattr(mcp_events._UDSPublisher, "put_nowait", _spy)
    return enqueued
