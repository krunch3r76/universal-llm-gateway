"""HTTP subscription seam: body shape, 422, timeout, SystemExit."""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from agent_bus_store.routes.threads.subscriptions import SubscribeBody
from cursor_bridge.grok_agent_subscriptions import (
    preflight_subscriptions,
    subscribe_grok,
    unsubscribe_grok,
)

pytestmark = pytest.mark.offline


class _Resp:
    def __init__(self, status: int, payload: Any) -> None:
        self.status_code = status
        self._payload = payload

    def json(self) -> Any:
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


class _Bus:
    def __init__(self, get: _Resp, post: _Resp | Exception) -> None:
        self._get = get
        self._post = post
        self.bodies: list[dict[str, Any]] = []

    def get(self, path: str) -> _Resp:
        del path
        return self._get

    def post(self, path: str, json: dict[str, Any]) -> _Resp:
        del path
        self.bodies.append(json)
        if isinstance(self._post, Exception):
            raise self._post
        return self._post

    def __enter__(self) -> _Bus:
        return self

    def __exit__(self, *_exc: object) -> bool:
        return False


def test_subscribe_body_matches_model(monkeypatch: pytest.MonkeyPatch) -> None:
    bus = _Bus(_Resp(200, {}), _Resp(200, {"appended": True}))
    monkeypatch.setattr("cursor_bridge.grok_agent_subscriptions._client", lambda: bus)
    out = subscribe_grok(
        mailbox="box", thread_scope="scope-1", display="9", agent_id="aid", title="Bot"
    )
    assert out["ok"] is True
    SubscribeBody(**bus.bodies[0])
    assert bus.bodies[0]["surface"] == "grok"
    assert bus.bodies[0]["display"] == "9"
    assert bus.bodies[0]["agent_id"] == "aid"


def test_subscribe_422_maps_code(monkeypatch: pytest.MonkeyPatch) -> None:
    bus = _Bus(
        _Resp(200, {}), _Resp(422, {"detail": {"code": "endpoint_thread_missing"}})
    )
    monkeypatch.setattr("cursor_bridge.grok_agent_subscriptions._client", lambda: bus)
    out = subscribe_grok(
        mailbox="box", thread_scope="missing", display="9", agent_id="aid", title="Bot"
    )
    assert out["ok"] is False
    assert out["code"] == "endpoint_thread_missing"


def test_subscribe_timeout_and_system_exit(monkeypatch: pytest.MonkeyPatch) -> None:
    bus = _Bus(_Resp(200, {}), httpx.TimeoutException("slow"))
    monkeypatch.setattr("cursor_bridge.grok_agent_subscriptions._client", lambda: bus)
    assert (
        subscribe_grok(
            mailbox="box", thread_scope="s", display="9", agent_id="a", title="t"
        )["code"]
        == "subscription_timeout"
    )

    def _boom() -> Any:
        raise SystemExit("no token")

    monkeypatch.setattr("cursor_bridge.grok_agent_subscriptions._client", _boom)
    assert preflight_subscriptions()["code"] == "subscription_unavailable"


def test_preflight_file_not_found(monkeypatch: pytest.MonkeyPatch) -> None:
    def _missing() -> Any:
        raise FileNotFoundError("sock")

    monkeypatch.setattr("cursor_bridge.grok_agent_subscriptions._client", _missing)
    assert preflight_subscriptions()["code"] == "subscription_unavailable"


def test_unsubscribe_keeps_touched_when_later_post_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rows = [
        {
            "state": "subscribed",
            "surface": "grok",
            "display": "9",
            "agent_id": "aid",
            "agent": "box",
            "thread_scope": "s",
        },
        {
            "state": "subscribed",
            "surface": "grok",
            "display": "9",
            "agent_id": "aid",
            "agent": "other",
            "thread_scope": "s2",
        },
    ]

    class _Seq:
        def __init__(self) -> None:
            self.posts: list[Any] = [_Resp(200, {}), httpx.ConnectError("down")]

        def get(self, path: str) -> _Resp:
            del path
            return _Resp(200, rows)

        def post(self, path: str, json: dict[str, Any]) -> _Resp:
            del path, json
            item = self.posts.pop(0)
            if isinstance(item, Exception):
                raise item
            return item

        def __enter__(self) -> _Seq:
            return self

        def __exit__(self, *_exc: object) -> bool:
            return False

    monkeypatch.setattr(
        "cursor_bridge.grok_agent_subscriptions._client", lambda: _Seq()
    )
    out = unsubscribe_grok(agent_id="aid", display="9")
    assert out["code"] == "unsubscribe_partial"
    assert out["unsubscribed"] == [{"agent": "box", "thread_scope": "s"}]
    assert out["unsubscribe_errors"][0]["agent"] == "other"


def test_unsubscribe_partial_then_clear(monkeypatch: pytest.MonkeyPatch) -> None:
    rows = [
        {
            "state": "subscribed",
            "surface": "grok",
            "display": "9",
            "agent_id": "aid",
            "agent": "box",
            "thread_scope": "s",
        }
    ]
    bus = _Bus(_Resp(200, rows), _Resp(500, {}))
    monkeypatch.setattr("cursor_bridge.grok_agent_subscriptions._client", lambda: bus)
    failed = unsubscribe_grok(agent_id="aid", display="9")
    assert failed["code"] == "unsubscribe_partial"
    bus_ok = _Bus(_Resp(200, []), _Resp(200, {}))
    monkeypatch.setattr(
        "cursor_bridge.grok_agent_subscriptions._client", lambda: bus_ok
    )
    cleared = unsubscribe_grok(agent_id="aid", display="9")
    assert cleared["ok"] is True
