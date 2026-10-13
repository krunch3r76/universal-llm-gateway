"""Tests for CDP pointer-without-link orphan sweep."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from agent_bus_store.cdp_pointer_sweep import (
    CDP_POINTER_ORPHAN_GRACE_SECONDS,
    format_cdp_generate_pointer_body,
    sweep_cdp_pointer_orphans,
)
from agent_bus_store.db import (
    admit_dispatch,
    create_thread,
    get_thread_with_links,
    get_turns,
    init_db,
    insert_turn,
)
from agent_bus_store.db.connection import connect


@pytest.fixture()
def bus_db(tmp_path, monkeypatch: pytest.MonkeyPatch):
    db_path = tmp_path / "bus.db"
    monkeypatch.setenv("AGENT_BUS_DB_PATH", str(db_path))
    init_db()
    yield db_path


def _old_timestamp(*, seconds_past_grace: int = 30) -> str:
    age = CDP_POINTER_ORPHAN_GRACE_SECONDS + seconds_past_grace
    return (datetime.now(UTC) - timedelta(seconds=age)).strftime("%Y-%m-%dT%H:%M:%SZ")


def _insert_orphan_pointer(
    thread_id: str,
    execution_id: str,
    *,
    created_at: str,
) -> None:
    body = format_cdp_generate_pointer_body(
        model="cdp/opus-5.5",
        execution_id=execution_id,
    )
    insert_turn(
        thread=thread_id,
        from_agent="dispatch",
        to_agent="web-anthropic",
        subject="cdp generate",
        body=body,
    )
    with connect() as conn:
        conn.execute(
            "UPDATE turns SET created_at = ? WHERE thread = ? AND body = ?",
            (created_at, thread_id, body),
        )


def test_orphan_past_grace_gets_failed_link_and_turn(bus_db) -> None:
    row = create_thread(thread_id=None, slug="orphan", lifecycle_state=None)
    assert row is not None
    thread_id = row["id"]
    execution_id = "exec-orphan-past-grace"
    _insert_orphan_pointer(
        thread_id,
        execution_id,
        created_at=_old_timestamp(),
    )

    assert sweep_cdp_pointer_orphans() == 1
    detail = get_thread_with_links(thread_id)
    assert detail is not None
    assert len(detail["dispatch_links"]) == 1
    link = detail["dispatch_links"][0]
    assert link["execution_id"] == execution_id
    assert link["terminal_status"] == "failed"
    turns = get_turns(thread=thread_id)
    assert any("cdp FAILED" in (t.get("subject") or "") for t in turns)


def test_pointer_inside_grace_untouched(bus_db) -> None:
    row = create_thread(thread_id=None, slug="young", lifecycle_state=None)
    assert row is not None
    thread_id = row["id"]
    _insert_orphan_pointer(
        thread_id,
        "exec-young",
        created_at=datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
    )

    assert sweep_cdp_pointer_orphans() == 0
    detail = get_thread_with_links(thread_id)
    assert detail is not None
    assert not detail["dispatch_links"]


def test_linked_pointer_untouched(bus_db) -> None:
    row = create_thread(thread_id=None, slug="linked", lifecycle_state="active")
    assert row is not None
    thread_id = row["id"]
    execution_id = "exec-linked"
    _insert_orphan_pointer(thread_id, execution_id, created_at=_old_timestamp())
    admit_dispatch(
        thread_id=thread_id,
        execution_id=execution_id,
        pipeline_id="cdp-generate",
    )

    assert sweep_cdp_pointer_orphans() == 0


def test_second_sweep_pass_idempotent(bus_db) -> None:
    row = create_thread(thread_id=None, slug="idem-sweep", lifecycle_state=None)
    assert row is not None
    thread_id = row["id"]
    _insert_orphan_pointer(
        thread_id,
        "exec-idem-sweep",
        created_at=_old_timestamp(),
    )

    assert sweep_cdp_pointer_orphans() == 1
    assert sweep_cdp_pointer_orphans() == 0


def test_null_lifecycle_thread_swept(bus_db) -> None:
    row = create_thread(thread_id=None, slug="null-lc", lifecycle_state=None)
    assert row is not None
    thread_id = row["id"]
    _insert_orphan_pointer(
        thread_id,
        "exec-null-lc",
        created_at=_old_timestamp(),
    )
    assert sweep_cdp_pointer_orphans() == 1


def test_two_orphans_same_thread_two_links(bus_db) -> None:
    row = create_thread(thread_id=None, slug="dual", lifecycle_state=None)
    assert row is not None
    thread_id = row["id"]
    _insert_orphan_pointer(
        thread_id,
        "exec-dual-a",
        created_at=_old_timestamp(),
    )
    _insert_orphan_pointer(
        thread_id,
        "exec-dual-b",
        created_at=_old_timestamp(seconds_past_grace=60),
    )

    assert sweep_cdp_pointer_orphans() == 2
    detail = get_thread_with_links(thread_id)
    assert detail is not None
    exec_ids = {link["execution_id"] for link in detail["dispatch_links"]}
    assert exec_ids == {"exec-dual-a", "exec-dual-b"}


def test_linked_batch_does_not_starve_newer_orphan(bus_db) -> None:
    """≥50 older linked pointers must not hide a later orphan (LIMIT after filter)."""
    created = _old_timestamp(seconds_past_grace=120)
    newer = _old_timestamp(seconds_past_grace=10)
    for i in range(60):
        row = create_thread(thread_id=None, slug=f"linked-{i}", lifecycle_state="active")
        assert row is not None
        execution_id = f"exec-linked-batch-{i}"
        _insert_orphan_pointer(row["id"], execution_id, created_at=created)
        admit_dispatch(
            thread_id=row["id"],
            execution_id=execution_id,
            pipeline_id="cdp-generate",
        )
    orphan = create_thread(thread_id=None, slug="starved-orphan", lifecycle_state=None)
    assert orphan is not None
    orphan_id = orphan["id"]
    _insert_orphan_pointer(orphan_id, "exec-starved-orphan", created_at=newer)

    assert sweep_cdp_pointer_orphans() == 1
    detail = get_thread_with_links(orphan_id)
    assert detail is not None
    assert detail["dispatch_links"][0]["terminal_status"] == "failed"
