"""Row-9: GET /turns honors after_turn (page 2 differs from page 1)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from agent_bus_store import create_app
from agent_bus_store.auth import require_token
from agent_bus_store.db import create_thread, init_db, insert_turn


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


def _seed_thread(*, n: int = 6) -> str:
    row = create_thread(thread_id=None, slug="row9-after-turn", tags=[])
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


def test_get_turns_after_turn_page2_differs_from_page1(bus_client) -> None:
    """Falsifiable AC conjunct: page 2 differs from page 1 under after_turn."""
    thread_id = _seed_thread(n=6)
    page1 = bus_client.get(
        "/turns",
        params={"thread": thread_id, "after_turn": 0, "last": 2},
    )
    assert page1.status_code == 200
    nums1 = [t["turn_number"] for t in page1.json()["turns"]]
    assert nums1 == [1, 2]

    page2 = bus_client.get(
        "/turns",
        params={"thread": thread_id, "after_turn": 2, "last": 2},
    )
    assert page2.status_code == 200
    nums2 = [t["turn_number"] for t in page2.json()["turns"]]
    assert nums2 == [3, 4]
    assert nums1 != nums2


def test_tip_window_unchanged_without_after_turn(bus_client) -> None:
    """Omit after_turn → newest-first tip (pre-row-9 contract)."""
    thread_id = _seed_thread(n=5)
    tip = bus_client.get(
        "/turns",
        params={"thread": thread_id, "last": 3},
    )
    assert tip.status_code == 200
    nums = [t["turn_number"] for t in tip.json()["turns"]]
    assert nums == [5, 4, 3]
