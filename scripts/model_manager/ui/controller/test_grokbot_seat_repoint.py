"""Unit tests for gc1 roster → grok-bot-gc1 endpoint subscription repoint."""

from __future__ import annotations

from typing import Any

import pytest

from scripts.model_manager.ui.controller.grokbot_egress_watch_logic import (
    GC1_ROSTER_NAME,
)
from scripts.model_manager.ui.controller.grokbot_seat_repoint import (
    run_gc1_repoint,
)


def _row(*, agent_id: str = "gc1-id", name: str = GC1_ROSTER_NAME) -> dict[str, Any]:
    return {"agent_id": agent_id, "name": name}


def test_not_unique_zero_rows_does_not_touch_db(monkeypatch: pytest.MonkeyPatch) -> None:
    subscribe_calls: list[dict[str, Any]] = []
    current_calls: list[tuple[str, str]] = []

    def fake_subscribe(**kwargs: Any) -> dict[str, Any]:
        subscribe_calls.append(kwargs)
        return {"appended": True}

    def fake_current(agent: str, thread_scope: str) -> dict[str, Any] | None:
        current_calls.append((agent, thread_scope))
        return None

    monkeypatch.setattr(
        "agent_bus_store.db.endpoint_subscriptions.subscribe_endpoint",
        fake_subscribe,
    )
    monkeypatch.setattr(
        "agent_bus_store.db.endpoint_subscriptions.current_subscription",
        fake_current,
    )

    result = run_gc1_repoint([_row(name="Impresario Alpha")])

    assert result == {"wrote": False, "reason": "not_unique"}
    assert subscribe_calls == []
    assert current_calls == []


def test_not_unique_two_gc1_rows_does_not_touch_db(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    subscribe_calls: list[dict[str, Any]] = []
    current_calls: list[tuple[str, str]] = []

    def fake_subscribe(**kwargs: Any) -> dict[str, Any]:
        subscribe_calls.append(kwargs)
        return {"appended": True}

    def fake_current(agent: str, thread_scope: str) -> dict[str, Any] | None:
        current_calls.append((agent, thread_scope))
        return None

    monkeypatch.setattr(
        "agent_bus_store.db.endpoint_subscriptions.subscribe_endpoint",
        fake_subscribe,
    )
    monkeypatch.setattr(
        "agent_bus_store.db.endpoint_subscriptions.current_subscription",
        fake_current,
    )

    rows = [_row(agent_id="a"), _row(agent_id="b")]
    result = run_gc1_repoint(rows)

    assert result == {"wrote": False, "reason": "not_unique"}
    assert subscribe_calls == []
    assert current_calls == []


def test_unchanged_agent_id_does_not_subscribe(monkeypatch: pytest.MonkeyPatch) -> None:
    subscribe_calls: list[dict[str, Any]] = []

    def fake_subscribe(**kwargs: Any) -> dict[str, Any]:
        subscribe_calls.append(kwargs)
        return {"appended": True}

    def fake_current(agent: str, thread_scope: str) -> dict[str, Any] | None:
        assert agent == "grok-bot-gc1"
        assert thread_scope == "15851"
        return {"agent_id": "same-id", "display": ":1"}

    monkeypatch.setattr(
        "agent_bus_store.db.endpoint_subscriptions.subscribe_endpoint",
        fake_subscribe,
    )
    monkeypatch.setattr(
        "agent_bus_store.db.endpoint_subscriptions.current_subscription",
        fake_current,
    )

    result = run_gc1_repoint([_row(agent_id="same-id")])

    assert result == {"wrote": False, "reason": "unchanged"}
    assert subscribe_calls == []


def test_different_agent_id_subscribes_once(monkeypatch: pytest.MonkeyPatch) -> None:
    subscribe_calls: list[dict[str, Any]] = []

    def fake_subscribe(**kwargs: Any) -> dict[str, Any]:
        subscribe_calls.append(kwargs)
        return {"appended": True}

    def fake_current(agent: str, thread_scope: str) -> dict[str, Any] | None:
        del agent, thread_scope
        return {"agent_id": "old-id", "display": ":2"}

    monkeypatch.setattr(
        "agent_bus_store.db.endpoint_subscriptions.subscribe_endpoint",
        fake_subscribe,
    )
    monkeypatch.setattr(
        "agent_bus_store.db.endpoint_subscriptions.current_subscription",
        fake_current,
    )

    result = run_gc1_repoint([_row(agent_id="new-id")])

    assert result == {"wrote": True}
    assert len(subscribe_calls) == 1
    call = subscribe_calls[0]
    assert call["agent"] == "grok-bot-gc1"
    assert call["thread_scope"] == "15851"
    assert call["agent_id"] == "new-id"
    assert call["surface"] == "grok"
    assert call["display"] == ":2"
    assert call["title"] == GC1_ROSTER_NAME
    assert call["bound_by"] == "grok-seat-repoint"


def test_missing_display_does_not_subscribe(monkeypatch: pytest.MonkeyPatch) -> None:
    subscribe_calls: list[dict[str, Any]] = []

    def fake_subscribe(**kwargs: Any) -> dict[str, Any]:
        subscribe_calls.append(kwargs)
        return {"appended": True}

    def fake_current(agent: str, thread_scope: str) -> dict[str, Any] | None:
        del agent, thread_scope
        return {"agent_id": "old-id", "display": ""}

    def fake_binding(agent: str) -> dict[str, Any] | None:
        del agent
        return None

    monkeypatch.setattr(
        "agent_bus_store.db.endpoint_subscriptions.subscribe_endpoint",
        fake_subscribe,
    )
    monkeypatch.setattr(
        "agent_bus_store.db.endpoint_subscriptions.current_subscription",
        fake_current,
    )
    monkeypatch.setattr(
        "cursor_bridge.grok_window_bindings.binding_for",
        fake_binding,
    )

    result = run_gc1_repoint([_row(agent_id="new-id")])

    assert result == {"wrote": False, "reason": "display_unknown"}
    assert subscribe_calls == []


def test_subscribe_exception_returns_subscribe_failed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class BoomError(RuntimeError):
        pass

    def fake_subscribe(**kwargs: Any) -> dict[str, Any]:
        del kwargs
        raise BoomError("nope")

    def fake_current(agent: str, thread_scope: str) -> dict[str, Any] | None:
        del agent, thread_scope
        return {"agent_id": "old-id", "display": ":1"}

    monkeypatch.setattr(
        "agent_bus_store.db.endpoint_subscriptions.subscribe_endpoint",
        fake_subscribe,
    )
    monkeypatch.setattr(
        "agent_bus_store.db.endpoint_subscriptions.current_subscription",
        fake_current,
    )

    result = run_gc1_repoint([_row(agent_id="new-id")])

    assert result == {
        "wrote": False,
        "reason": "subscribe_failed",
        "error": "BoomError",
    }
