"""Close-on-read for persistent auto-provisioned generate consult threads."""

from __future__ import annotations

import pytest
from agent_bus_store import create_app
from agent_bus_store.auth import require_token
from agent_bus_store.close_on_read import (
    CLOSE_ON_READ_TAG,
    append_close_on_read_marker,
    maybe_close_generate_thread_on_read,
)
from agent_bus_store.db import (
    admit_dispatch,
    create_thread_with_turn,
    get_thread,
    init_db,
    insert_turn,
    mark_turn_read,
)
from agent_bus_store.db.connection import connect
from agent_bus_store.db.threads import set_thread_tags
from agent_bus_store.disposition import append_bus_lifecycle_tags
from agent_bus_store.turns_models import ThreadStatus
from fastapi.testclient import TestClient


@pytest.fixture()
def bus_db(tmp_path, monkeypatch: pytest.MonkeyPatch):
    db_path = tmp_path / "bus.db"
    monkeypatch.setenv("AGENT_BUS_DB_PATH", str(db_path))
    init_db()
    return db_path


@pytest.fixture()
def bus_client(bus_db):
    app = create_app(db_path=str(bus_db))
    app.dependency_overrides[require_token] = lambda: None
    client = TestClient(app)
    yield client
    app.dependency_overrides.clear()


def _generate_consult_tags(*, persistent: bool = True) -> list[str]:
    tags = append_close_on_read_marker(
        append_bus_lifecycle_tags(
            ["agent:reviewer", "type:generate", "contract:consult"],
            bus_lifecycle="persistent" if persistent else "ephemeral",
        ),
        bus_lifecycle="persistent" if persistent else "ephemeral",
    )
    assert CLOSE_ON_READ_TAG in tags
    return tags


def _seed_on_behalf_generate_thread(
    bus_db,
    *,
    tags: list[str] | None = None,
    caller: str = "claude-web",
    role: str = "reviewer",
    summary: str | None = None,
) -> tuple[str, int, int]:
    """Pointer turn 1 (caller→role) + result turn 2 (role→caller), active lifecycle."""
    thread_row, pointer_id, *_ = create_thread_with_turn(
        slug="generate-consult",
        from_agent=caller,
        to_agent=role,
        subject="reviewer generate — req-1",
        body="pointer body",
        summary=summary,
        lifecycle_state="pending",
    )
    thread_id = thread_row["id"]
    with connect() as conn:
        set_thread_tags(conn, thread_id, tags or _generate_consult_tags())
    admit_dispatch(
        thread_id=thread_id,
        execution_id="exec-close-on-read",
        pipeline_id="frontier-dispatch",
        caller_agent=caller,
    )
    result_id, _, _ = insert_turn(
        thread=thread_id,
        from_agent=role,
        to_agent=caller,
        subject="reviewer reply — req-1",
        body="Generate result body.",
    )
    return thread_id, pointer_id, result_id


def test_append_close_on_read_marker_requires_generate_and_persistent() -> None:
    tagged = append_close_on_read_marker(
        append_bus_lifecycle_tags(["type:generate"], bus_lifecycle="persistent"),
        bus_lifecycle="persistent",
    )
    assert CLOSE_ON_READ_TAG in tagged

    ephemeral = append_close_on_read_marker(
        append_bus_lifecycle_tags(["type:generate"], bus_lifecycle="ephemeral"),
        bus_lifecycle="ephemeral",
    )
    assert CLOSE_ON_READ_TAG not in ephemeral

    non_generate = append_close_on_read_marker(
        append_bus_lifecycle_tags(["type:handoff"], bus_lifecycle="persistent"),
        bus_lifecycle="persistent",
    )
    assert CLOSE_ON_READ_TAG not in non_generate


def test_persistent_generate_thread_closes_when_result_turn_read(bus_db) -> None:
    """AC1: marked persistent generate thread completes after result read."""
    thread_id, pointer_id, result_id = _seed_on_behalf_generate_thread(bus_db)

    assert mark_turn_read(pointer_id) is not None
    detail = get_thread(thread_id)
    assert detail is not None
    assert detail["status"] == ThreadStatus.ACTIVE
    assert detail["bus_lifecycle_state"] == "active"

    assert mark_turn_read(result_id) is not None
    detail = get_thread(thread_id)
    assert detail is not None
    assert detail["status"] == ThreadStatus.CLOSED
    assert detail["bus_lifecycle_state"] == "completed"
    assert detail["unread_count"] == 0
    # Empty prior ⇒ leave summary unset (¬ machine one-liner).
    assert not (detail.get("summary") or "").strip()


def test_close_on_read_preserves_so_what_summary(bus_db) -> None:
    so_what = "ULG: close-on-read keeps outcome title"
    thread_id, _, result_id = _seed_on_behalf_generate_thread(
        bus_db, summary=so_what
    )
    assert mark_turn_read(result_id) is not None
    detail = get_thread(thread_id)
    assert detail is not None
    assert detail["status"] == ThreadStatus.CLOSED
    assert detail["summary"] == f"DONE — {so_what}"
    assert "auto-closed" not in (detail["summary"] or "")


def test_unread_result_turn_keeps_thread_open(bus_db) -> None:
    """AC3: unread on-behalf result leaves the thread active."""
    thread_id, _, _ = _seed_on_behalf_generate_thread(bus_db)
    detail = get_thread(thread_id)
    assert detail is not None
    assert detail["bus_lifecycle_state"] == "active"
    assert maybe_close_generate_thread_on_read(thread_id) is None


def test_later_unread_turn_blocks_close(bus_db) -> None:
    """AC3: a later unread turn prevents close-on-read."""
    thread_id, _, result_id = _seed_on_behalf_generate_thread(bus_db)
    insert_turn(
        thread=thread_id,
        from_agent="dispatch",
        to_agent="claude-web",
        subject="follow-up",
        body="later turn",
    )
    mark_turn_read(result_id)
    detail = get_thread(thread_id)
    assert detail is not None
    assert detail["status"] == ThreadStatus.ACTIVE
    assert detail["bus_lifecycle_state"] == "active"


def test_ephemeral_generate_thread_not_closed_by_hook(bus_db) -> None:
    """AC2: ephemeral generate threads stay open on read (delivery path closes them)."""
    tags = append_bus_lifecycle_tags(
        ["agent:reviewer", "type:generate", "contract:consult"],
        bus_lifecycle="ephemeral",
    )
    assert CLOSE_ON_READ_TAG not in tags
    thread_id, _, result_id = _seed_on_behalf_generate_thread(bus_db, tags=tags)
    mark_turn_read(result_id)
    detail = get_thread(thread_id)
    assert detail is not None
    assert detail["status"] == ThreadStatus.ACTIVE
    assert detail["bus_lifecycle_state"] == "active"


def test_non_generate_persistent_thread_not_closed(bus_db) -> None:
    """AC2: explicit persistent non-generate threads are untouched."""
    tags = append_bus_lifecycle_tags(
        ["agent:reviewer", "type:handoff", "contract:consult"],
        bus_lifecycle="persistent",
    )
    thread_id, _, result_id = _seed_on_behalf_generate_thread(bus_db, tags=tags)
    mark_turn_read(result_id)
    detail = get_thread(thread_id)
    assert detail is not None
    assert detail["status"] == ThreadStatus.ACTIVE
    assert detail["bus_lifecycle_state"] == "active"


def test_route_mark_read_closes_generate_thread(bus_client, bus_db) -> None:
    """AC1/AC6: PATCH /turns/{id}/read closes marked persistent generate thread."""
    thread_id, _, result_id = _seed_on_behalf_generate_thread(bus_db)

    resp = bus_client.patch(f"/turns/{result_id}/read")
    assert resp.status_code == 200, resp.text

    detail = bus_client.get(f"/threads/{thread_id}").json()
    assert detail["status"] == ThreadStatus.CLOSED
    assert detail["bus_lifecycle_state"] == "completed"
    assert detail["unread_count"] == 0

