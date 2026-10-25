"""Impresario repoint R1 — unnumbered wildcard ownership and contested logging."""

from __future__ import annotations

from typing import Any

import pytest

from scripts.model_manager.ui.controller.grokbot_seat_repoint import (
    run_impresario_repoint,
)


def _subscribe_fixtures(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    subscribe_calls: list[dict[str, Any]] = []

    def fake_subscribe(**kwargs: Any) -> dict[str, Any]:
        subscribe_calls.append(kwargs)
        return {"appended": True}

    def fake_current(agent: str, thread_scope: str) -> dict[str, Any] | None:
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
        "scripts.model_manager.ui.controller.grokbot_seat_repoint._live_impresario_display",
        lambda: "10",
    )
    return subscribe_calls


def test_unnumbered_and_numbered_coexist_wildcard_to_unnumbered_only(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    subscribe_calls = _subscribe_fixtures(monkeypatch)
    rows = [
        {"name": "Impresario Career", "agent_id": "agent-a"},
        {"name": "Impresario Career (16999)", "agent_id": "agent-b"},
    ]
    with caplog.at_level("WARNING"):
        run_impresario_repoint(rows)
    keys = {(c["agent"], c["thread_scope"]) for c in subscribe_calls}
    assert ("impresario-career", "*") in keys
    assert any(
        c["agent"] == "impresario-career"
        and c["thread_scope"] == "*"
        and c["agent_id"] == "agent-a"
        for c in subscribe_calls
    )
    assert ("impresario-career", "16999") in keys
    assert ("impresario-career-16999", "*") in keys
    assert ("impresario-career-16999", "16999") in keys
    contested = [
        r
        for r in caplog.records
        if "impresario repoint contested" in r.getMessage()
    ]
    assert contested == []


def test_two_numbered_rows_contest_wildcard_once(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Two numbered impresarios share (impresario-career, *) with no unnumbered owner."""
    subscribe_calls = _subscribe_fixtures(monkeypatch)
    rows = [
        {"name": "Impresario Career (16999)", "agent_id": "agent-a"},
        {"name": "Impresario Career (17000)", "agent_id": "agent-b"},
    ]
    with caplog.at_level("WARNING"):
        run_impresario_repoint(rows)
    assert not any(
        c["agent"] == "impresario-career" and c["thread_scope"] == "*"
        for c in subscribe_calls
    )
    contested = [
        r
        for r in caplog.records
        if "impresario repoint contested" in r.getMessage()
    ]
    assert len(contested) == 1
    assert "impresario-career" in contested[0].getMessage()
    assert "*" in contested[0].getMessage()
