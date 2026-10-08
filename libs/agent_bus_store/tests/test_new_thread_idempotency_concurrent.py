"""Concurrent idempotency replay — turn 1 must not duplicate under parallel send."""

from __future__ import annotations

import threading
import time
from unittest.mock import patch

from fastapi.testclient import TestClient

from agent_bus_store import create_app
from agent_bus_store.auth import require_token
from agent_bus_store.routes.threads import new_thread_send as new_thread_send_mod


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
