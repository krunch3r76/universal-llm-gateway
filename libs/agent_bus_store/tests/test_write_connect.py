"""Tests for the in-process FIFO write ticket and ``write_connect``."""

from __future__ import annotations

import sqlite3
import threading
import time
from unittest.mock import patch

import pytest

from agent_bus_store.db.connection import connect, init_db, write_connect
from agent_bus_store.db import write_ticket as wt


@pytest.fixture()
def isolated_db(tmp_path, monkeypatch):
    """Point agent-bus SQLite at a temp file and initialize schema."""
    db_file = tmp_path / "messages.db"
    monkeypatch.setenv("AGENT_BUS_DB_PATH", str(db_file))
    init_db()
    yield db_file


def test_two_threads_serialize_without_timeout(isolated_db):
    """AC-B1: second writer commits only after the first releases."""
    barrier = threading.Barrier(2, timeout=5)
    order: list[str] = []

    def first():
        with write_connect() as conn:
            order.append("first-enter")
            barrier.wait()
            time.sleep(0.05)
            conn.execute(
                "INSERT INTO thread_meta (key, value) VALUES (?, ?)",
                ("t1", "1"),
            )
            order.append("first-exit")

    def second():
        barrier.wait()
        with write_connect() as conn:
            order.append("second-enter")
            conn.execute(
                "INSERT INTO thread_meta (key, value) VALUES (?, ?)",
                ("t2", "2"),
            )
            order.append("second-exit")

    t1 = threading.Thread(target=first)
    t2 = threading.Thread(target=second)
    t1.start()
    t2.start()
    t1.join(timeout=10)
    t2.join(timeout=10)
    assert not t1.is_alive() and not t2.is_alive()
    assert order.index("first-exit") < order.index("second-enter")


def test_nested_write_connect_one_connection_one_begin(isolated_db, monkeypatch):
    """AC-B2: nested calls reuse one connection and one queue event."""
    monkeypatch.delenv("AGENT_BUS_WRITE_QUEUE_DEPTH_THRESHOLD", raising=False)
    with write_connect() as outer:
        assert outer.isolation_level is None
        with write_connect() as inner:
            assert inner is outer
        assert wt.outer_depth() == 1
    assert wt.outer_depth() == 0


def test_failure_before_acquire_unblocks_next(isolated_db):
    """AC-B3: ``cancel_wait`` on the head sets the next waiter."""
    second = threading.Event()
    head = threading.Event()
    try:
        with wt._mutex:
            wt._deque.clear()
            wt._deque.append(head)
            wt._deque.append(second)
        wt.cancel_wait(head)
        assert second.is_set()
    finally:
        with wt._mutex:
            wt._deque.clear()
        st = wt._state()
        st.depth = 0
        st.conn = None
        st.event = None


def test_read_connect_does_not_take_ticket(isolated_db, monkeypatch):
    """AC-B5: ``connect()`` readers bypass the write deque."""
    monkeypatch.setenv("AGENT_BUS_WRITE_QUEUE_DEPTH_THRESHOLD", "1")
    armed: list[int] = []

    with patch(
        "agent_bus_store.events.write_queue.emit_write_queue_armed",
        side_effect=lambda **kw: armed.append(kw["depth"]),
    ):
        with write_connect():
            with connect() as conn:
                conn.execute("SELECT COUNT(*) FROM threads").fetchone()
    assert armed == []


def test_unset_busy_timeout_and_queue_signals(isolated_db, monkeypatch):
    """AC-B7 / AC-C1: unset env skips pragma and queue signals."""
    monkeypatch.delenv("AGENT_BUS_WRITE_BUSY_TIMEOUT_MS", raising=False)
    monkeypatch.delenv("AGENT_BUS_WRITE_QUEUE_DEPTH_THRESHOLD", raising=False)
    signals: list[str] = []

    with patch(
        "agent_bus_store.events.write_queue.emit_write_queue_armed",
        side_effect=lambda **_: signals.append("armed"),
    ), patch(
        "agent_bus_store.events.write_queue.emit_write_queue_cleared",
        side_effect=lambda **_: signals.append("cleared"),
    ):
        with write_connect() as conn:
            conn.execute(
                "INSERT INTO thread_meta (key, value) VALUES (?, ?)",
                ("k", "v"),
            )
    assert signals == []


def test_configured_busy_timeout_pragma(isolated_db, monkeypatch):
    """AC-C1: positive env sets ``PRAGMA busy_timeout`` to that value."""
    monkeypatch.setenv("AGENT_BUS_WRITE_BUSY_TIMEOUT_MS", "2500")
    with write_connect() as conn:
        timeout_row = conn.execute("PRAGMA busy_timeout").fetchone()
        conn.execute(
            "INSERT INTO thread_meta (key, value) VALUES (?, ?)",
            ("k", "v"),
        )
    assert timeout_row is not None and int(timeout_row[0]) == 2500


def test_queue_armed_and_cleared_edges(isolated_db, monkeypatch):
    """AC-B7: threshold 1 emits armed then cleared."""
    monkeypatch.setenv("AGENT_BUS_WRITE_QUEUE_DEPTH_THRESHOLD", "1")
    events: list[tuple[str, int]] = []
    gate = threading.Event()

    def armed(**kw):
        events.append(("armed", kw["depth"]))

    def cleared(**kw):
        events.append(("cleared", kw["depth"]))

    def second():
        gate.wait(timeout=5)
        with write_connect() as conn:
            conn.execute(
                "INSERT INTO thread_meta (key, value) VALUES (?, ?)",
                ("b", "2"),
            )

    with patch(
        "agent_bus_store.db.write_ticket.emit_write_queue_armed", side_effect=armed
    ), patch(
        "agent_bus_store.db.write_ticket.emit_write_queue_cleared",
        side_effect=cleared,
    ):
        t = threading.Thread(target=second)
        t.start()
        with write_connect() as conn:
            gate.set()
            time.sleep(0.05)
            conn.execute(
                "INSERT INTO thread_meta (key, value) VALUES (?, ?)",
                ("a", "1"),
            )
        t.join(timeout=10)
    assert ("armed", 2) in events
    assert any(sig == "cleared" and depth <= 1 for sig, depth in events)


def test_write_connection_begin_immediate_after_pragmas(isolated_db, monkeypatch):
    """AC-B4: write path uses autocommit mode and an open transaction after enter."""
    monkeypatch.delenv("AGENT_BUS_WRITE_BUSY_TIMEOUT_MS", raising=False)
    with write_connect() as conn:
        assert conn.isolation_level is None
        assert conn.in_transaction
