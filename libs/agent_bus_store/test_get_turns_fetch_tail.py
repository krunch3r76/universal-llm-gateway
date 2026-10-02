"""a:37201 — fetch(last=N) tip is index-tail and does not take the write FIFO."""

from __future__ import annotations

import threading
import time
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from agent_bus_store import create_app
from agent_bus_store.auth import require_token
from agent_bus_store.db import (
    create_thread,
    get_turn_by_number,
    get_turns,
    init_db,
    insert_turn,
)
from agent_bus_store.db import write_ticket as wt
from agent_bus_store.db.connection import write_connect


@pytest.fixture()
def bus_client(tmp_path, monkeypatch: pytest.MonkeyPatch):
    db_path = tmp_path / "bus.db"
    monkeypatch.setenv("AGENT_BUS_DB_PATH", str(db_path))
    init_db()
    app = create_app()
    app.dependency_overrides[require_token] = lambda: None
    with TestClient(app) as client:
        yield client
    app.dependency_overrides.clear()


def _seed_thread(*, n: int, slug: str = "fetch-tail") -> str:
    row = create_thread(thread_id=None, slug=slug, tags=[])
    assert row is not None
    thread_id = row["id"]
    for i in range(1, n + 1):
        insert_turn(
            thread=thread_id,
            from_agent="cursor",
            to_agent="web-anthropic",
            subject=f"turn-{i}",
            body=f"body-{i}",
            status="open",
        )
    return thread_id


def test_high_turn_tip_last_three_compact(bus_client) -> None:
    """≥~1900 turns: last=3 compact returns tip without scanning the whole thread."""
    n = 1900
    thread_id = _seed_thread(n=n, slug="high-turn-tip")
    tip = bus_client.get(
        "/turns",
        params={"thread": thread_id, "last": 3, "compact": "true"},
    )
    assert tip.status_code == 200
    turns = tip.json()["turns"]
    assert [t["turn_number"] for t in turns] == [n, n - 1, n - 2]
    assert all(t.get("body") in (None, "") for t in turns)


def test_empty_thread_last_returns_empty(bus_client) -> None:
    row = create_thread(thread_id=None, slug="empty-tip", tags=[])
    assert row is not None
    tip = bus_client.get(
        "/turns",
        params={"thread": row["id"], "last": 3, "compact": "true"},
    )
    assert tip.status_code == 200
    assert tip.json()["turns"] == []


def test_last_greater_than_len_returns_all(bus_client) -> None:
    thread_id = _seed_thread(n=2, slug="short-tip")
    tip = bus_client.get(
        "/turns",
        params={"thread": thread_id, "last": 10, "compact": "true"},
    )
    assert tip.status_code == 200
    nums = [t["turn_number"] for t in tip.json()["turns"]]
    assert nums == [2, 1]


def test_fetch_during_held_write_ticket_does_not_block(tmp_path, monkeypatch) -> None:
    """Pure fetch must use connect(), not enqueue on the write FIFO."""
    db_path = tmp_path / "bus.db"
    monkeypatch.setenv("AGENT_BUS_DB_PATH", str(db_path))
    init_db()
    thread_id = _seed_thread(n=20, slug="concurrent-fetch")

    barrier = threading.Barrier(2, timeout=5)
    fetch_done = threading.Event()
    fetch_error: list[BaseException] = []

    def hold_write() -> None:
        with write_connect():
            barrier.wait()
            # Stay in the write ticket until the reader finishes.
            assert fetch_done.wait(timeout=5)

    def do_fetch() -> None:
        try:
            barrier.wait()
            rows = get_turns(thread=thread_id, last=3, compact=True)
            assert [r["turn_number"] for r in rows] == [20, 19, 18]
            fetch_done.set()
        except BaseException as exc:  # noqa: BLE001 — surface to main thread
            fetch_error.append(exc)
            fetch_done.set()

    writer = threading.Thread(target=hold_write)
    reader = threading.Thread(target=do_fetch)
    writer.start()
    reader.start()
    reader.join(timeout=5)
    writer.join(timeout=5)
    assert not reader.is_alive(), "fetch blocked behind write ticket"
    assert fetch_error == []


def test_mark_read_still_takes_write_ticket(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "bus.db"
    monkeypatch.setenv("AGENT_BUS_DB_PATH", str(db_path))
    init_db()
    thread_id = _seed_thread(n=3, slug="mark-read-write")

    entered = threading.Event()
    release = threading.Event()
    second_started = threading.Event()
    order: list[str] = []

    def first() -> None:
        with write_connect():
            order.append("first-enter")
            entered.set()
            assert release.wait(timeout=5)
            order.append("first-exit")

    def second_mark_read() -> None:
        assert entered.wait(timeout=5)
        second_started.set()
        rows = get_turns(thread=thread_id, last=1, mark_read=True)
        assert len(rows) == 1
        order.append("second-done")

    t1 = threading.Thread(target=first)
    t2 = threading.Thread(target=second_mark_read)
    t1.start()
    t2.start()
    assert second_started.wait(timeout=5)
    # Give the mark_read path time to block on the ticket if it correctly waits.
    time.sleep(0.1)
    assert "second-done" not in order
    release.set()
    t1.join(timeout=5)
    t2.join(timeout=5)
    assert order[-1] == "second-done"


def test_pure_fetch_does_not_call_enqueue(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "bus.db"
    monkeypatch.setenv("AGENT_BUS_DB_PATH", str(db_path))
    init_db()
    thread_id = _seed_thread(n=5, slug="no-enqueue")

    with patch.object(wt, "enqueue_and_wait", side_effect=AssertionError("write ticket")):
        rows = get_turns(thread=thread_id, last=2, compact=True)
    assert [r["turn_number"] for r in rows] == [5, 4]


def test_append_during_tip_fetch_sees_consistent_window(bus_client) -> None:
    """Concurrent append must not break tip ordering for an in-flight last=N."""
    thread_id = _seed_thread(n=10, slug="append-during")
    tip = bus_client.get(
        "/turns",
        params={"thread": thread_id, "last": 3, "compact": "true"},
    )
    assert tip.status_code == 200
    before = [t["turn_number"] for t in tip.json()["turns"]]
    assert before == [10, 9, 8]

    insert_turn(
        thread=thread_id,
        from_agent="cursor",
        to_agent="web-anthropic",
        subject="turn-11",
        body="body-11",
        status="open",
    )
    tip2 = bus_client.get(
        "/turns",
        params={"thread": thread_id, "last": 3, "compact": "true"},
    )
    assert tip2.status_code == 200
    assert [t["turn_number"] for t in tip2.json()["turns"]] == [11, 10, 9]


def test_get_turn_by_number_during_held_write_ticket_does_not_block(
    tmp_path, monkeypatch
) -> None:
    """a:37201 review A1 — hop fallback get(turn_number) must not take write FIFO."""
    db_path = tmp_path / "bus.db"
    monkeypatch.setenv("AGENT_BUS_DB_PATH", str(db_path))
    init_db()
    thread_id = _seed_thread(n=5, slug="get-by-number-concurrent")

    barrier = threading.Barrier(2, timeout=5)
    fetch_done = threading.Event()
    fetch_error: list[BaseException] = []

    def hold_write() -> None:
        with write_connect():
            barrier.wait()
            assert fetch_done.wait(timeout=5)

    def do_get() -> None:
        try:
            barrier.wait()
            row = get_turn_by_number(thread_id, 5)
            assert row is not None
            assert row["turn_number"] == 5
            fetch_done.set()
        except BaseException as exc:  # noqa: BLE001
            fetch_error.append(exc)
            fetch_done.set()

    writer = threading.Thread(target=hold_write)
    reader = threading.Thread(target=do_get)
    writer.start()
    reader.start()
    reader.join(timeout=5)
    writer.join(timeout=5)
    assert not reader.is_alive(), "get_turn_by_number blocked behind write ticket"
    assert fetch_error == []


def test_get_turn_by_number_does_not_call_enqueue(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "bus.db"
    monkeypatch.setenv("AGENT_BUS_DB_PATH", str(db_path))
    init_db()
    thread_id = _seed_thread(n=3, slug="get-by-number-no-enqueue")

    with patch.object(wt, "enqueue_and_wait", side_effect=AssertionError("write ticket")):
        row = get_turn_by_number(thread_id, 2)
    assert row is not None
    assert row["turn_number"] == 2
