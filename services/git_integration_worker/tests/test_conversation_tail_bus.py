"""Bus-backed transcript tail when no sdk run-line log exists."""

from __future__ import annotations

import sqlite3

import pytest

from services.git_integration_worker.cursor_sdk_run_lines import (
    _bus_turns_sqlite,
    clear_conversation_tail_bus_cache,
    conversation_tail,
)


def test_bus_turns_sqlite_reads_after_turn(tmp_path, monkeypatch) -> None:
    db = tmp_path / "bus.db"
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE turns (turn_number INT, from_agent TEXT, to_agent TEXT, "
        "subject TEXT, body TEXT, thread TEXT)"
    )
    conn.execute(
        "INSERT INTO turns VALUES (1,'a','b','hi','body1','42'),"
        "(2,'c','d','ok','body2','42')"
    )
    conn.commit()
    conn.close()
    monkeypatch.setenv("AGENT_BUS_DB_PATH", str(db))
    rows = _bus_turns_sqlite("42", 1)
    assert len(rows) == 1
    assert rows[0]["turn_number"] == 2


def test_conversation_tail_falls_back_to_bus(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db = tmp_path / "bus.db"
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE turns (turn_number INT, from_agent TEXT, to_agent TEXT, "
        "subject TEXT, body TEXT, thread TEXT)"
    )
    conn.execute(
        "INSERT INTO turns VALUES (1,'synthesizer','ide','reply','hello','99')"
    )
    conn.commit()
    conn.close()
    monkeypatch.setenv("AGENT_BUS_DB_PATH", str(db))
    monkeypatch.setenv("AGENT_BUS_TOKEN", "")

    from services.git_integration_worker.cursor_dispatch_ledger import (
        CursorDispatchLedger,
    )

    led = CursorDispatchLedger.instance()
    with led._connect() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO cursor_sdk_dispatches "
            "(dispatch_id, fingerprint, thread_id, resolved_model, status, "
            "message_present, read_only, record_json) "
            "VALUES ('tail-bus-test', 'fp', '99', 'anthropic/claude', 'admitted', 0, 0, '{}')"
        )
        conn.commit()

    payload = conversation_tail("tail-bus-test", 0)
    assert payload["source"] == "agent-bus.turns"
    assert payload["lines"]
    assert "hello" in payload["lines"][0]["text"]


def test_terminal_tail_cached_zero_bus_repeat(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db = tmp_path / "bus.db"
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE turns (turn_number INT, from_agent TEXT, to_agent TEXT, "
        "subject TEXT, body TEXT, thread TEXT)"
    )
    conn.execute(
        "CREATE TABLE threads (id TEXT, status TEXT)"
    )
    conn.execute(
        "INSERT INTO turns VALUES (1,'synthesizer','ide','reply','cached body','88')"
    )
    conn.execute("INSERT INTO threads VALUES ('88', 'open')")
    conn.commit()
    conn.close()
    monkeypatch.setenv("AGENT_BUS_DB_PATH", str(db))
    monkeypatch.setenv("AGENT_BUS_TOKEN", "")
    clear_conversation_tail_bus_cache()

    from services.git_integration_worker.cursor_dispatch_ledger import (
        CursorDispatchLedger,
    )

    led = CursorDispatchLedger.instance()
    with led._connect() as lconn:
        lconn.execute(
            "INSERT OR REPLACE INTO cursor_sdk_dispatches "
            "(dispatch_id, fingerprint, thread_id, resolved_model, status, "
            "message_present, read_only, record_json) "
            "VALUES ('tail-cache-test', 'fp', '88', 'anthropic/claude', 'completed', "
            "0, 0, '{}')"
        )
        lconn.commit()

    fetch_calls = 0

    def counting_fetch(thread_id: str, after_turn: int) -> list[dict]:
        nonlocal fetch_calls
        fetch_calls += 1
        return _bus_turns_sqlite(thread_id, after_turn)

    monkeypatch.setattr(
        "services.git_integration_worker.cursor_sdk_run_lines._fetch_bus_turns",
        counting_fetch,
    )

    first = conversation_tail("tail-cache-test", 0)
    second = conversation_tail("tail-cache-test", 0)
    assert first == second
    assert fetch_calls == 1


def test_open_tail_refetch_after_ttl(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db = tmp_path / "bus.db"
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE turns (turn_number INT, from_agent TEXT, to_agent TEXT, "
        "subject TEXT, body TEXT, thread TEXT)"
    )
    conn.execute("CREATE TABLE threads (id TEXT, status TEXT)")
    conn.execute(
        "INSERT INTO turns VALUES (1,'synthesizer','ide','reply','live','55')"
    )
    conn.execute("INSERT INTO threads VALUES ('55', 'open')")
    conn.commit()
    conn.close()
    monkeypatch.setenv("AGENT_BUS_DB_PATH", str(db))
    monkeypatch.setenv("AGENT_BUS_TOKEN", "")
    clear_conversation_tail_bus_cache()

    from services.git_integration_worker.cursor_dispatch_ledger import (
        CursorDispatchLedger,
    )

    led = CursorDispatchLedger.instance()
    with led._connect() as lconn:
        lconn.execute(
            "INSERT OR REPLACE INTO cursor_sdk_dispatches "
            "(dispatch_id, fingerprint, thread_id, resolved_model, status, "
            "message_present, read_only, record_json) "
            "VALUES ('tail-open-test', 'fp', '55', 'anthropic/claude', 'running', "
            "0, 0, '{}')"
        )
        lconn.commit()

    fetch_calls = 0

    def counting_fetch(thread_id: str, after_turn: int) -> list[dict]:
        nonlocal fetch_calls
        fetch_calls += 1
        return _bus_turns_sqlite(thread_id, after_turn)

    monkeypatch.setattr(
        "services.git_integration_worker.cursor_sdk_run_lines._fetch_bus_turns",
        counting_fetch,
    )
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_sdk_run_lines._OPEN_BUS_TAIL_TTL_S",
        0.0,
    )
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_sdk_supersede.live_run_for_dispatch",
        lambda _d: None,
    )

    first = conversation_tail("tail-open-test", 0)
    second = conversation_tail("tail-open-test", 0)
    assert first["source"] == second["source"] == "agent-bus.turns"
    assert fetch_calls == 2
