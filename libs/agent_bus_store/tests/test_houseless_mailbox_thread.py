"""Houseless mailbox thread predicate."""

from __future__ import annotations

import pytest
from agent_bus_store.db import create_thread, init_db
from agent_bus_store.db.lane_associations import associate_lane
from agent_bus_store.houseless_mailbox_thread import is_houseless_mailbox_thread

pytestmark = pytest.mark.offline


def test_houseless_mailbox_is_parentless_work_thread(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_path = tmp_path / "bus.db"
    monkeypatch.setenv("AGENT_BUS_DB_PATH", str(db_path))
    init_db()
    detail = create_thread(thread_id=None, slug="mailbox-only")
    assert detail is not None
    assert is_houseless_mailbox_thread(str(detail["id"]))


def test_root_house_is_not_houseless_mailbox(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_path = tmp_path / "bus.db"
    monkeypatch.setenv("AGENT_BUS_DB_PATH", str(db_path))
    init_db()
    detail = create_thread(thread_id=None, slug="root-house", tags=["role:root"])
    assert detail is not None
    assert not is_houseless_mailbox_thread(str(detail["id"]))


def test_child_lane_is_not_houseless_mailbox(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_path = tmp_path / "bus.db"
    monkeypatch.setenv("AGENT_BUS_DB_PATH", str(db_path))
    init_db()
    parent = create_thread(thread_id=None, slug="parent", tags=["role:root"])
    child = create_thread(thread_id=None, slug="child")
    assert parent is not None and child is not None
    associate_lane(
        thread_id=str(child["id"]),
        parent_thread_id=str(parent["id"]),
        lane_role="side",
        bound_by="test",
    )
    assert not is_houseless_mailbox_thread(str(child["id"]))
