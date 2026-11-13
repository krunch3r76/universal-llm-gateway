"""Houseless mailbox thread predicate."""

from __future__ import annotations

import pytest
from agent_bus_store.db import create_thread, init_db
from agent_bus_store.db.lane_associations import associate_lane
from agent_bus_store.houseless_mailbox_thread import (
    is_houseless_mailbox_thread,
    resolve_mailbox_thread,
)

pytestmark = pytest.mark.offline


def test_houseless_mailbox_is_parentless_work_thread(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_path = tmp_path / "bus.db"
    monkeypatch.setenv("AGENT_BUS_DB_PATH", str(db_path))
    init_db()
    detail = create_thread(thread_id=None, slug="mailbox-only", tags=["mailbox"])
    assert detail is not None
    assert is_houseless_mailbox_thread(str(detail["id"]))


def test_parentless_without_mailbox_tag_is_not_houseless(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_path = tmp_path / "bus.db"
    monkeypatch.setenv("AGENT_BUS_DB_PATH", str(db_path))
    init_db()
    detail = create_thread(thread_id=None, slug="work-not-mailbox")
    assert detail is not None
    assert not is_houseless_mailbox_thread(str(detail["id"]))


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


def test_resolve_mailbox_thread_explicit(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    db_path = tmp_path / "bus.db"
    monkeypatch.setenv("AGENT_BUS_DB_PATH", str(db_path))
    init_db()
    detail = create_thread(thread_id=None, slug="explicit-mb", tags=["mailbox"])
    assert detail is not None
    tid = str(detail["id"])
    assert (
        resolve_mailbox_thread(
            mailbox_thread=tid, caller_agent=None, thread_id=None
        )
        == tid
    )


def test_resolve_mailbox_thread_from_caller_slug(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_path = tmp_path / "bus.db"
    monkeypatch.setenv("AGENT_BUS_DB_PATH", str(db_path))
    init_db()
    detail = create_thread(thread_id=None, slug="my-seat-mailbox", tags=["mailbox"])
    assert detail is not None
    resolved = resolve_mailbox_thread(
        mailbox_thread=None,
        caller_agent="my-seat-mailbox",
        thread_id=None,
    )
    assert resolved == str(detail["id"])


def test_resolve_mailbox_thread_ambiguous_caller_returns_none(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_path = tmp_path / "bus.db"
    monkeypatch.setenv("AGENT_BUS_DB_PATH", str(db_path))
    init_db()
    create_thread(thread_id=None, slug="dup-slug", tags=["mailbox"])
    create_thread(thread_id=None, slug="dup-slug", tags=["mailbox"])
    assert (
        resolve_mailbox_thread(
            mailbox_thread=None, caller_agent="dup-slug", thread_id=None
        )
        is None
    )
