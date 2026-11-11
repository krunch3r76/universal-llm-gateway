"""Closeout reply_to must not echo cursor-sdk when commissioner is known."""

from __future__ import annotations

import pytest
from agent_bus_store.db import create_thread, init_db, insert_turn

from services.git_integration_worker.cursor_sdk_closeout_reply_to import (
    resolve_sdk_closeout_reply_to,
)


@pytest.fixture()
def bus_db(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "bus.db"
    monkeypatch.setenv("AGENT_BUS_DB_PATH", str(path))
    init_db()


def test_specimen_74_class_prefers_admit_author_over_cursor_sdk(
    bus_db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    thread = "22796"
    create_thread(thread_id=thread, slug="closeout-74")
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_sdk_closeout_reply_to."
        "_agent_has_live_subscription",
        lambda _agent, _tid: True,
    )
    insert_turn(
        thread=thread,
        from_agent="web-anthropic",
        to_agent="dispatch",
        subject="cursor-sdk generate admitted",
        body="admit",
    )
    assert (
        resolve_sdk_closeout_reply_to(
            caller_agent="cursor-sdk", thread_id=thread, bus=None
        )
        == "web-anthropic"
    )


def test_fallback_dispatch_when_no_commissioner(bus_db: None) -> None:
    assert (
        resolve_sdk_closeout_reply_to(
            caller_agent="cursor-sdk", thread_id="22796", bus=None
        )
        == "dispatch"
    )


def test_unsubscribed_admit_author_falls_back_dispatch_rely_on_reroute(
    bus_db: None,
) -> None:
    """22796 admit bodies are work_key-only; author without subscription → dispatch."""
    thread = "22796"
    create_thread(thread_id=thread, slug="closeout-reroute")
    insert_turn(
        thread=thread,
        from_agent="web-anthropic",
        to_agent="dispatch",
        subject="cursor-sdk generate — f784a5e5",
        body="work_key: todo:example",
    )
    assert (
        resolve_sdk_closeout_reply_to(
            caller_agent="cursor-sdk", thread_id=thread, bus=None
        )
        == "dispatch"
    )
