"""Dispatcher transport: pipeline_error, retry, claim debounce latency pin."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from bus_watch.endpoint_wake_dispatcher import _POLL_S, _orion_post
from bus_watch.endpoint_wake_transport import StoreClient, classify_orion

pytestmark = pytest.mark.offline


def test_orion_post_pipeline_error_retries_once_then_pending() -> None:
    rows = [
        {
            "thread_id": "21207",
            "turn_id": 1,
            "turn_number": 69,
            "agent": "orion",
            "from_agent": "maestro",
        }
    ]
    sub = {
        "surface": "chatgpt",
        "title": "https://chatgpt.com/c/6ae2e1bc-0c94-83e8-9f21-62467c4935d5",
        "id": 1,
    }
    calls: list[int] = []

    def _fake_post(url: str, *, json: dict, headers: dict) -> MagicMock:
        calls.append(1)
        resp = MagicMock()
        resp.status_code = 500
        resp.json.return_value = {"error": "pipeline failed"}
        return resp

    client = MagicMock()
    client.post.side_effect = _fake_post
    client.__enter__ = lambda self: self
    client.__exit__ = lambda *a: None

    with patch(
        "bus_watch.endpoint_wake_dispatcher.make_sync_client", return_value=client
    ):
        state, reason = _orion_post(rows, sub, "69", from_agent="maestro", wake_id="w1")
    assert len(calls) == 2
    assert state == "pending"
    assert reason == "pipeline_error:500"


def test_orion_post_turn_ids_list_in_payload() -> None:
    rows = [
        {
            "thread_id": "21207",
            "turn_id": 10,
            "turn_number": 69,
            "agent": "orion",
            "from_agent": "maestro",
        },
        {
            "thread_id": "21207",
            "turn_id": 11,
            "turn_number": 70,
            "agent": "orion",
            "from_agent": "maestro",
        },
    ]
    sub = {"surface": "chatgpt", "title": "https://chatgpt.com/c/x", "id": 2}
    captured: dict = {}

    def _ok_post(url: str, *, json: dict, headers: dict) -> MagicMock:
        captured["options"] = json["pipeline_options"]
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {
            "choices": [{"message": {"content": '{"ok": true, "delivered": true}'}}],
        }
        return resp

    client = MagicMock()
    client.post.side_effect = _ok_post
    client.__enter__ = lambda self: self
    client.__exit__ = lambda *a: None

    with patch(
        "bus_watch.endpoint_wake_dispatcher.make_sync_client", return_value=client
    ):
        _orion_post(rows, sub, "69,70", from_agent="maestro", wake_id="w2")
    opts = captured["options"]
    assert opts["turn"] == "69,70"
    assert opts["turn_ids"] == [10, 11]


def test_classify_orion_non_dict_is_pipeline_error_not_unparseable() -> None:
    assert classify_orion(None) == ("pending", "pipeline_error")
    assert classify_orion("<html>") == ("pending", "pipeline_error")


def test_claim_debounce_latency_setting() -> None:
    """Dispatcher claim body uses orion floor; server applies per-kind debounce windows."""
    from agent_bus_store.db.endpoint_wake_ledger import (
        ENDPOINT_WAKE_DEBOUNCE_S_DEFAULT,
        ENDPOINT_WAKE_DEBOUNCE_S_ORION_CHAT,
        claim_due_wakes,
    )

    assert claim_due_wakes.__defaults__ is not None
    debounce_default = claim_due_wakes.__defaults__[0]
    assert debounce_default == ENDPOINT_WAKE_DEBOUNCE_S_DEFAULT
    assert ENDPOINT_WAKE_DEBOUNCE_S_ORION_CHAT == 30
    assert _POLL_S == 2.0
    captured: dict = {}

    class _BusStub:
        def request(self, method: str, path: str, **kwargs: object) -> MagicMock:
            captured.update(kwargs.get("json") or {})  # type: ignore[arg-type]
            resp = MagicMock()
            resp.status_code = 200
            resp.json.return_value = {"wake_id": None, "rows": []}
            return resp

    with patch("bus_watch.endpoint_wake_transport.make_sync_client") as msc:
        ctx = MagicMock()
        ctx.__enter__.return_value = _BusStub()
        ctx.__exit__.return_value = None
        msc.return_value = ctx
        StoreClient().claim()
    assert captured.get("debounce_s") == ENDPOINT_WAKE_DEBOUNCE_S_ORION_CHAT
