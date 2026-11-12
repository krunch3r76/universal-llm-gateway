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


def test_frozen_tail_repeat_skips_ledger_and_bus(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    clear_conversation_tail_bus_cache()
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_sdk_supersede.live_run_for_dispatch",
        lambda _d: None,
    )
    db = tmp_path / "bus.db"
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE turns (turn_number INT, from_agent TEXT, to_agent TEXT, "
        "subject TEXT, body TEXT, thread TEXT)"
    )
    conn.execute(
        "INSERT INTO turns VALUES (1,'synthesizer','ide','reply','body','77')"
    )
    conn.commit()
    conn.close()
    monkeypatch.setenv("AGENT_BUS_DB_PATH", str(db))
    monkeypatch.setenv("AGENT_BUS_TOKEN", "")

    from services.git_integration_worker.cursor_dispatch_ledger import (
        CursorDispatchLedger,
    )

    led = CursorDispatchLedger.instance()
    with led._connect() as lconn:
        lconn.execute(
            "INSERT OR REPLACE INTO cursor_sdk_dispatches "
            "(dispatch_id, fingerprint, thread_id, resolved_model, status, "
            "message_present, read_only, record_json) "
            "VALUES ('frozen-tail', 'fp', '77', 'anthropic/claude', 'completed', "
            "0, 0, '{}')"
        )
        lconn.commit()

    ledger_calls = 0

    def counting_ledger(dispatch_id: str):
        nonlocal ledger_calls
        ledger_calls += 1
        return ("77", "completed")

    monkeypatch.setattr(
        "services.git_integration_worker.cursor_sdk_run_lines._ledger_row",
        counting_ledger,
    )
    bus_fetches = 0

    def guarded_fetch(thread_id: str, after_turn: int):
        nonlocal bus_fetches
        bus_fetches += 1
        return [
            {
                "turn_number": 1,
                "from_agent": "a",
                "subject": "s",
                "body": "once",
            }
        ]

    monkeypatch.setattr(
        "services.git_integration_worker.cursor_sdk_run_lines._fetch_bus_turns",
        guarded_fetch,
    )

    first = conversation_tail("frozen-tail", 0)
    second = conversation_tail("frozen-tail", 0)
    assert bus_fetches == 1
    assert first == second
    assert ledger_calls == 1


def test_admitted_then_running_refetches_tail(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    clear_conversation_tail_bus_cache()
    monkeypatch.setenv("AGENT_BUS_TOKEN", "")
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_sdk_supersede.live_run_for_dispatch",
        lambda _d: None,
    )
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_sdk_run_lines._OPEN_BUS_TAIL_TTL_S",
        999.0,
    )

    from services.git_integration_worker.cursor_dispatch_ledger import (
        CursorDispatchLedger,
    )

    led = CursorDispatchLedger.instance()
    with led._connect() as lconn:
        lconn.execute(
            "INSERT OR REPLACE INTO cursor_sdk_dispatches "
            "(dispatch_id, fingerprint, thread_id, resolved_model, status, "
            "message_present, read_only, record_json) "
            "VALUES ('admit-run', 'fp', '55', 'anthropic/claude', 'admitted', "
            "0, 0, '{}')"
        )
        lconn.commit()

    bodies = ["first-body", "second-body"]
    fetch_calls = 0

    def counting_fetch(thread_id: str, after_turn: int):
        nonlocal fetch_calls
        fetch_calls += 1
        return [
            {
                "turn_number": 1,
                "from_agent": "a",
                "subject": "s",
                "body": bodies[min(fetch_calls - 1, 1)],
            }
        ]

    monkeypatch.setattr(
        "services.git_integration_worker.cursor_sdk_run_lines._fetch_bus_turns",
        counting_fetch,
    )

    conversation_tail("admit-run", 0)
    with led._connect() as lconn:
        lconn.execute(
            "UPDATE cursor_sdk_dispatches SET status='running' WHERE dispatch_id='admit-run'"
        )
        lconn.commit()
    payload = conversation_tail("admit-run", 0)
    assert fetch_calls == 2
    assert "second-body" in payload["lines"][0]["text"]


def test_bus_tail_cache_bounded(monkeypatch: pytest.MonkeyPatch) -> None:
    import services.git_integration_worker.cursor_sdk_run_lines as rlines

    monkeypatch.setattr(rlines, "_BUS_TAIL_CACHE_MAX", 4)
    rlines._bus_tail_cache = rlines.LruDict(4)
    rlines.clear_conversation_tail_bus_cache()

    for i in range(6):
        key = (f"d-{i}", 0)
        rlines._bus_tail_cache_store(
            key,
            (0.0, {"lines": [], "cursor": 0, "eof": True, "source": "x"}, True, "completed"),
        )
    assert len(rlines._bus_tail_cache) == 4
