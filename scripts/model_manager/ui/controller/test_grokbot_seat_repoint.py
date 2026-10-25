"""Unit tests for gc1 roster → grok-bot-gc1 endpoint subscription repoint."""

from __future__ import annotations

from typing import Any

import pytest

from scripts.model_manager.ui.controller.grokbot_egress_watch_logic import (
    GC1_ROSTER_NAME,
)
from scripts.model_manager.ui.controller.grokbot_seat_repoint import (
    run_gc1_repoint,
    run_impresario_repoint,
)


def _row(*, agent_id: str = "gc1-id", name: str = GC1_ROSTER_NAME) -> dict[str, Any]:
    return {"agent_id": agent_id, "name": name}


def test_not_unique_zero_rows_does_not_touch_db(
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


def test_impresario_repoint_subscribes_wildcard_and_thread_scope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    subscribe_calls: list[dict[str, Any]] = []

    def fake_subscribe(**kwargs: Any) -> dict[str, Any]:
        subscribe_calls.append(kwargs)
        return {"appended": True}

    def fake_current(agent: str, thread_scope: str) -> dict[str, Any] | None:
        del agent, thread_scope
        return None

    def fake_binding(agent: str) -> dict[str, Any] | None:
        del agent
        return {"display": ":9"}

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

    rows = [
        {
            "name": "gc1-pilot-impresario-fleet-action-line",
            "agent_id": "imp-fleet",
        },
        {
            "name": "Impresario Career",
            "agent_id": "imp-career",
            "description": "mailbox impresario-career",
        },
        {
            "name": "Impresario AAB (16934)",
            "agent_id": "imp-aab",
        },
    ]
    result = run_impresario_repoint(rows)

    assert result["wrote"] is True
    agents = {(c["agent"], c["thread_scope"]) for c in subscribe_calls}
    assert ("gc1-pilot-impresario-fleet-action-line", "*") in agents
    assert ("impresario-career", "*") in agents
    assert ("impresario-aab", "*") in agents
    assert ("impresario-aab", "16934") in agents
    assert all(c["surface"] == "grok" for c in subscribe_calls)
    assert all(c["bound_by"] == "grok-seat-repoint" for c in subscribe_calls)


def test_impresario_repoint_skips_retired_and_duplicate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    subscribe_calls: list[dict[str, Any]] = []

    def fake_subscribe(**kwargs: Any) -> dict[str, Any]:
        subscribe_calls.append(kwargs)
        return {"appended": True}

    def fake_current(agent: str, thread_scope: str) -> dict[str, Any] | None:
        del agent, thread_scope
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
        lambda _a: {"display": ":9"},
    )

    rows = [
        {"name": "Impresario Dup", "agent_id": "a"},
        {"name": "Impresario Dup", "agent_id": "b"},
        {"name": "Impresario (retired)", "agent_id": "old"},
    ]
    result = run_impresario_repoint(rows)

    assert result["wrote"] is False
    assert result["subscribed"] == []
    assert subscribe_calls == []


def test_impresario_repoint_unchanged_agent_id_noop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No-op when journal agent_id and display match binding_for / live window (:9)."""
    subscribe_calls: list[dict[str, Any]] = []

    def fake_subscribe(**kwargs: Any) -> dict[str, Any]:
        subscribe_calls.append(kwargs)
        return {"appended": True}

    def fake_current(agent: str, thread_scope: str) -> dict[str, Any] | None:
        if agent == "impresario-career" and thread_scope == "*":
            return {"agent_id": "same-id", "state": "subscribed", "display": ":9"}
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
        lambda _a: {"display": ":9"},
    )

    rows = [{"name": "Impresario Career", "agent_id": "same-id"}]
    result = run_impresario_repoint(rows)

    assert result["wrote"] is False
    assert result["subscribed"] == []
    assert subscribe_calls == []


def test_impresario_repoint_skips_slug_claimed_by_two_active_rows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    subscribe_calls: list[dict[str, Any]] = []

    def fake_subscribe(**kwargs: Any) -> dict[str, Any]:
        subscribe_calls.append(kwargs)
        return {"appended": True}

    def fake_current(agent: str, thread_scope: str) -> dict[str, Any] | None:
        del agent, thread_scope
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
        lambda _a: {"display": ":9"},
    )

    rows = [
        {"name": "Impresario Career", "agent_id": "a"},
        {"name": "Impresario-Career", "agent_id": "b"},
    ]
    result = run_impresario_repoint(rows)

    assert result["wrote"] is False
    assert subscribe_calls == []


def test_impresario_repoint_follows_live_window_after_promote(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    subscribe_calls: list[dict[str, Any]] = []

    def fake_subscribe(**kwargs: Any) -> dict[str, Any]:
        subscribe_calls.append(kwargs)
        return {"appended": True}

    def fake_current(agent: str, thread_scope: str) -> dict[str, Any] | None:
        if agent == "impresario-career" and thread_scope == "*":
            return {
                "agent_id": "imp-career",
                "state": "subscribed",
                "display": ":9",
            }
        return None

    def fake_binding(agent: str) -> dict[str, Any] | None:
        if agent == "impresario":
            return {"display": ":10"}
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

    rows = [{"name": "Impresario Career", "agent_id": "imp-career"}]
    result = run_impresario_repoint(rows)

    assert result["wrote"] is True
    assert len(subscribe_calls) == 1
    assert subscribe_calls[0]["display"] == ":10"


def test_impresario_repoint_unsubscribes_retired_row_matching_agent_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    unsubscribe_calls: list[dict[str, Any]] = []
    subscribe_calls: list[dict[str, Any]] = []

    def fake_unsubscribe(**kwargs: Any) -> dict[str, Any]:
        unsubscribe_calls.append(kwargs)
        return {"appended": True}

    def fake_subscribe(**kwargs: Any) -> dict[str, Any]:
        subscribe_calls.append(kwargs)
        return {"appended": True}

    def fake_current(agent: str, thread_scope: str) -> dict[str, Any] | None:
        if agent == "impresario-career" and thread_scope == "*":
            return {"agent_id": "old", "state": "subscribed", "display": ":9"}
        return None

    monkeypatch.setattr(
        "agent_bus_store.db.endpoint_subscriptions.unsubscribe_endpoint",
        fake_unsubscribe,
    )
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
        lambda _a: {"display": ":9"},
    )

    rows = [{"name": "Impresario Career (retired)", "agent_id": "old"}]
    result = run_impresario_repoint(rows)

    assert result["wrote"] is True
    assert len(unsubscribe_calls) == 1
    assert unsubscribe_calls[0]["agent"] == "impresario-career"
    assert unsubscribe_calls[0]["expected_agent_id"] == "old"
    assert subscribe_calls == []

    unsubscribe_calls.clear()

    def fake_current_none(agent: str, thread_scope: str) -> dict[str, Any] | None:
        del agent, thread_scope
        return None

    monkeypatch.setattr(
        "agent_bus_store.db.endpoint_subscriptions.current_subscription",
        fake_current_none,
    )
    result2 = run_impresario_repoint(rows)
    assert result2["wrote"] is False
    assert unsubscribe_calls == []

    def fake_current_other(agent: str, thread_scope: str) -> dict[str, Any] | None:
        if agent == "impresario-career" and thread_scope == "*":
            return {"agent_id": "other", "state": "subscribed", "display": ":9"}
        return None

    monkeypatch.setattr(
        "agent_bus_store.db.endpoint_subscriptions.current_subscription",
        fake_current_other,
    )
    result3 = run_impresario_repoint(rows)
    assert result3["wrote"] is False
    assert unsubscribe_calls == []
