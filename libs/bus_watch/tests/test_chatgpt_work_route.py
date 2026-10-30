"""Dispatcher chatgpt_work route uses explicit conversation URL."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from agent_bus_store.db import create_thread, init_db
from agent_bus_store.db.endpoint_subscriptions import insert_chatgpt_work_row_for_test
from bus_watch.endpoint_wake_dispatcher import _chatgpt_work_route

pytestmark = pytest.mark.offline

_HOME = "910001"
_INST = "inst-a"
_URL = "https://chatgpt.com/g/g-p-proj-a/c/aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"


@pytest.fixture()
def db(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENT_BUS_DB_PATH", str(tmp_path / "bus.db"))
    monkeypatch.delenv("AGENT_BUS_ENDPOINT_WAKE_DISPATCHER", raising=False)
    init_db()


def test_work_route_uses_explicit_url_not_glass(db: None) -> None:
    create_thread(thread_id=_HOME, slug="work-route")
    insert_chatgpt_work_row_for_test(
        instance_id=_INST,
        home_house_id=_HOME,
        conversation_url=_URL,
    )
    with patch("cursor_bridge.wake_delivery.normalize_target") as norm:
        route = _chatgpt_work_route("orion", _HOME)
        norm.assert_not_called()
    assert route is not None
    _, sub = route
    assert sub["title"] == _URL
