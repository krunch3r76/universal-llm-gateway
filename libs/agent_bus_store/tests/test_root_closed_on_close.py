"""Store-path ``manage.charter.tick.root_closed`` on enrolled-root close/unenroll."""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from agent_bus_store import create_app
from agent_bus_store.auth import require_token
from agent_bus_store.db import create_thread, init_db
from agent_bus_store.db.threads import remove_tags, update_thread
from agent_bus_store.db.threads_atomic import close_thread
from agent_bus_store.enrollment_guard import ENROLLMENT_TAG


@pytest.fixture()
def bus_db(tmp_path, monkeypatch: pytest.MonkeyPatch):
    db_path = tmp_path / "bus.db"
    monkeypatch.setenv("AGENT_BUS_DB_PATH", str(db_path))
    init_db()
    app = create_app()
    app.dependency_overrides[require_token] = lambda: None
    yield TestClient(app)
    app.dependency_overrides.clear()


@pytest.fixture()
def capture_root_closed(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []

    def _capture(signal: str, payload: dict[str, Any], *, role: str = "observation") -> None:
        calls.append({"signal": signal, "payload": payload, "role": role})

    monkeypatch.setattr("agent_bus_store.events.thread_closed._publish", _capture)
    return calls


def _enrolled_root() -> str:
    detail = create_thread(
        thread_id=None,
        slug="root-closed-emit",
        tags=["role:root", "type:discussion", ENROLLMENT_TAG, "project:ulg"],
        enroll_charter_runner=True,
    )
    return str(detail["id"])


def test_close_thread_strips_enrollment_and_emits_root_closed(
    bus_db, capture_root_closed
) -> None:
    thread_id = _enrolled_root()
    detail = close_thread(thread_id, summary="arc done")
    assert detail is not None
    assert detail["status"] == "closed"
    assert ENROLLMENT_TAG not in list(detail.get("tags") or [])

    closed = [
        c for c in capture_root_closed if c["signal"] == "mcp.agentbus.thread.closed"
    ]
    roots = [
        c
        for c in capture_root_closed
        if c["signal"] == "manage.charter.tick.root_closed"
    ]
    assert len(closed) == 1
    assert closed[0]["payload"]["thread"] == thread_id
    assert len(roots) == 1
    assert roots[0]["payload"]["root"] == thread_id
    assert roots[0]["payload"]["closed"] is True
    assert roots[0]["payload"]["unenrolled"] is True
    assert roots[0]["payload"]["reason"] == "close_while_enrolled"


def test_close_thread_without_enrollment_skips_root_closed(
    bus_db, capture_root_closed
) -> None:
    detail = create_thread(
        thread_id=None,
        slug="plain-close",
        tags=["type:discussion", "project:ulg"],
    )
    thread_id = str(detail["id"])
    close_thread(thread_id)
    assert not any(
        c["signal"] == "manage.charter.tick.root_closed" for c in capture_root_closed
    )
    assert any(
        c["signal"] == "mcp.agentbus.thread.closed" for c in capture_root_closed
    )


def test_update_thread_status_closed_strips_enrollment(
    bus_db, capture_root_closed
) -> None:
    thread_id = _enrolled_root()
    detail = update_thread(thread_id, status="closed", summary="via update")
    assert detail is not None
    assert ENROLLMENT_TAG not in list(detail.get("tags") or [])
    roots = [
        c
        for c in capture_root_closed
        if c["signal"] == "manage.charter.tick.root_closed"
    ]
    assert len(roots) == 1
    assert roots[0]["payload"]["root"] == thread_id


def test_remove_tags_on_closed_emits_root_closed(bus_db, capture_root_closed) -> None:
    thread_id = _enrolled_root()
    # Close without going through the new strip path: force status then remove tag.
    update_thread(
        thread_id,
        status="closed",
        tags=["role:root", "type:discussion", ENROLLMENT_TAG, "project:ulg"],
    )
    capture_root_closed.clear()
    detail = remove_tags(thread_id, [ENROLLMENT_TAG])
    assert detail is not None
    assert ENROLLMENT_TAG not in list(detail.get("tags") or [])
    roots = [
        c
        for c in capture_root_closed
        if c["signal"] == "manage.charter.tick.root_closed"
    ]
    assert len(roots) == 1
    assert roots[0]["payload"]["reason"] == "unenroll_after_close"
