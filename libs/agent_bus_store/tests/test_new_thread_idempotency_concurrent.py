"""Concurrent idempotency replay — turn 1 must not duplicate under parallel send."""

from __future__ import annotations

import threading
import time
from unittest.mock import patch

from agent_bus_store import create_app
from agent_bus_store.auth import require_token
from agent_bus_store.db import write_ticket
from agent_bus_store.routes.threads import new_thread_send as new_thread_send_mod
from fastapi.testclient import TestClient


def _store_client(tmp_path) -> TestClient:
    app = create_app(db_path=str(tmp_path / "bus.db"))
    app.dependency_overrides[require_token] = lambda: None
    return TestClient(app)


def test_new_thread_idempotency_key_concurrent_replay_single_turn(tmp_path) -> None:
    """Two parallel sends with the same idempotency_key must leave exactly one turn 1."""
    gate = threading.Event()
    release = threading.Event()
    original_insert = new_thread_send_mod.insert_turn

    def gated_insert(*args, **kwargs):
        if not gate.is_set():
            gate.set()
            assert release.wait(timeout=10)
        return original_insert(*args, **kwargs)

    payload = {
        "new_slug": "concurrent-idem-slug",
        "from": "cursor",
        "to": "web-anthropic",
        "subject": "s",
        "body": "b",
        "idempotency_key": "concurrent-idem-key-abcdef12",
    }
    statuses: list[int] = []
    thread_ids: list[str] = []

    def post_once(client: TestClient) -> None:
        resp = client.post("/threads/send", json=payload)
        statuses.append(resp.status_code)
        if resp.status_code in (200, 201):
            thread_ids.append(resp.json()["thread"]["id"])

    with _store_client(tmp_path) as client_a:
        client_b = _store_client(tmp_path)
        try:
            with patch.object(
                new_thread_send_mod, "insert_turn", side_effect=gated_insert
            ):
                t1 = threading.Thread(target=post_once, args=(client_a,))
                t2 = threading.Thread(target=post_once, args=(client_b,))
                t1.start()
                assert gate.wait(timeout=5)
                t2.start()
                time.sleep(0.05)
                release.set()
                t1.join(timeout=15)
                t2.join(timeout=15)

            assert len(set(thread_ids)) == 1
            thread_id = thread_ids[0]
            turns = client_a.get("/turns", params={"thread": thread_id}).json()[
                "turns"
            ]
            assert len(turns) == 1
            assert sorted(statuses) == [200, 201]
        finally:
            client_b.close()


def test_failed_first_send_replay_inserts_turn_one(tmp_path) -> None:
    """Mint committed and turn insert failed must not wedge the idempotency key."""
    original = new_thread_send_mod.insert_turn
    calls = {"n": 0}

    def flaky(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("turn insert failed")
        return original(*args, **kwargs)

    payload = {
        "new_slug": "failed-then-replay",
        "from": "cursor",
        "to": "web-anthropic",
        "subject": "s",
        "body": "b",
        "idempotency_key": "failed-first-send-key-12",
    }
    started = time.monotonic()
    with _store_client(tmp_path) as client:
        with patch.object(new_thread_send_mod, "insert_turn", side_effect=flaky):
            first = client.post("/threads/send", json=payload)
            second = client.post("/threads/send", json=payload)
        elapsed = time.monotonic() - started
        assert first.status_code == 500, first.text
        assert second.status_code == 200, second.text
        assert second.json().get("idempotent_replay") is True
        thread_id = second.json()["thread"]["id"]
        turns = client.get("/turns", params={"thread": thread_id}).json()["turns"]
        assert len(turns) == 1
        assert turns[0]["turn_number"] == 1
        assert elapsed < 5.0


def test_fresh_idempotent_send_write_tickets_match_plain_send(tmp_path) -> None:
    """A fresh keyed send must not take the extra pre-insert write lookups."""

    def _tickets(client: TestClient, payload: dict) -> int:
        count = {"n": 0}
        original = write_ticket.enqueue_and_wait

        def counted():
            count["n"] += 1
            return original()

        with patch.object(write_ticket, "enqueue_and_wait", counted):
            resp = client.post("/threads/send", json=payload)
        assert resp.status_code == 201, resp.text
        return count["n"]

    with _store_client(tmp_path) as client:
        plain = _tickets(
            client,
            {
                "new_slug": "plain-ticket-slug",
                "from": "cursor",
                "to": "web-anthropic",
                "subject": "s",
                "body": "b",
            },
        )
        keyed = _tickets(
            client,
            {
                "new_slug": "keyed-ticket-slug",
                "from": "cursor",
                "to": "web-anthropic",
                "subject": "s",
                "body": "b",
                "idempotency_key": "fresh-send-one-ticket-1",
            },
        )
    assert keyed == plain


def _sidecar_files(tmp_path, monkeypatch) -> None:
    cortex_root = tmp_path / "cortex-files"
    cortex_root.mkdir()
    monkeypatch.setenv("CORTEX_FILES_ROOT", str(cortex_root))
    import cortex_store.dispatch_ops._thread_sidecar as sidecar_mod

    monkeypatch.setattr(sidecar_mod, "_FILES_ROOT", cortex_root)


def test_sidecar_idempotency_key_concurrent_replay_single_turn(
    tmp_path, monkeypatch
) -> None:
    """Parallel new_thread sidecar sends with one key leave a single turn 1."""
    _sidecar_files(tmp_path, monkeypatch)
    gate = threading.Event()
    release = threading.Event()
    import agent_bus_store.db.turns as turns_mod

    original_insert = turns_mod.insert_turn

    def gated_insert(*args, **kwargs):
        if not gate.is_set():
            gate.set()
            assert release.wait(timeout=10)
        return original_insert(*args, **kwargs)

    payload = {
        "new_slug": "concurrent-sidecar-slug",
        "from": "cursor",
        "to": "web-anthropic",
        "subject": "s",
        "body": "b",
        "sidecar_content": "sidecar body",
        "idempotency_key": "concurrent-sidecar-key-34",
    }
    statuses: list[int] = []
    thread_ids: list[str] = []

    def post_once(client: TestClient) -> None:
        resp = client.post("/threads/send", json=payload)
        statuses.append(resp.status_code)
        if resp.status_code in (200, 201):
            thread_ids.append(resp.json()["thread"]["id"])

    with _store_client(tmp_path) as client_a:
        client_b = _store_client(tmp_path)
        try:
            with patch.object(turns_mod, "insert_turn", side_effect=gated_insert):
                t1 = threading.Thread(target=post_once, args=(client_a,))
                t2 = threading.Thread(target=post_once, args=(client_b,))
                t1.start()
                assert gate.wait(timeout=5)
                t2.start()
                time.sleep(0.05)
                release.set()
                t1.join(timeout=15)
                t2.join(timeout=15)
            assert len(set(thread_ids)) == 1, statuses
            thread_id = thread_ids[0]
            turns = client_a.get("/turns", params={"thread": thread_id}).json()[
                "turns"
            ]
            assert len(turns) == 1
            assert turns[0]["turn_number"] == 1
            assert sorted(statuses) == [200, 201]
        finally:
            client_b.close()


def test_failed_first_sidecar_send_replay_inserts_turn_one(
    tmp_path, monkeypatch
) -> None:
    """Sidecar path: insert failure after mint still accepts the keyed retry."""
    _sidecar_files(tmp_path, monkeypatch)
    import agent_bus_store.db.turns as turns_mod

    original = turns_mod.insert_turn
    calls = {"n": 0}

    def flaky(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("turn insert failed")
        return original(*args, **kwargs)

    payload = {
        "new_slug": "failed-sidecar-replay",
        "from": "cursor",
        "to": "web-anthropic",
        "subject": "s",
        "body": "b",
        "sidecar_content": "sidecar body",
        "idempotency_key": "failed-sidecar-key-5678",
    }
    started = time.monotonic()
    with _store_client(tmp_path) as client:
        with patch.object(turns_mod, "insert_turn", side_effect=flaky):
            first = client.post("/threads/send", json=payload)
            second = client.post("/threads/send", json=payload)
        elapsed = time.monotonic() - started
        assert first.status_code == 500, first.text
        assert second.status_code == 200, second.text
        thread_id = second.json()["thread"]["id"]
        turns = client.get("/turns", params={"thread": thread_id}).json()["turns"]
        assert len(turns) == 1
        assert turns[0]["turn_number"] == 1
        assert elapsed < 5.0
