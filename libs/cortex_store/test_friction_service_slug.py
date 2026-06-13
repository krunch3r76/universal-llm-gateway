"""Tests for friction/frictions service slug normalization (friction 16851)."""

from __future__ import annotations

import pytest

from cortex_store.dispatch_ops._shared import normalize_service_slug, service_entity_id
from cortex_store.dispatch_ops.ops_assertions import _op_frictions
from cortex_store.dispatch_ops.ops_assertions_write import _op_friction


def test_normalize_service_slug_bare() -> None:
    assert normalize_service_slug("mcp-server") == "mcp-server"


def test_normalize_service_slug_entity_id() -> None:
    assert normalize_service_slug("service:mcp-server") == "mcp-server"


def test_service_entity_id_from_both_forms() -> None:
    assert service_entity_id("mcp-server") == "service:mcp-server"
    assert service_entity_id("service:mcp-server") == "service:mcp-server"


def test_op_frictions_accepts_qualified_service(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def fake_list(**kwargs: object) -> dict[str, object]:
        captured.update(kwargs)
        return {"items": []}

    monkeypatch.setattr(
        "cortex_store.dispatch_ops.ops_assertions._list_assertions_impl",
        fake_list,
    )
    _op_frictions(service="service:mcp-server")
    assert captured["entity_id"] == "service:mcp-server"


def test_op_friction_accepts_qualified_service(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def fake_create(body: dict[str, object]) -> dict[str, object]:
        captured.update(body)
        return {"item": {"id": 1}}

    monkeypatch.setattr(
        "cortex_store.dispatch_ops.ops_assertions_write._create_assertion_impl",
        fake_create,
    )
    monkeypatch.setattr(
        "cortex_store.dispatch_ops.ops_assertions_write.record",
        lambda *a, **k: None,
    )

    result = _op_friction(
        service="service:agent-bus",
        category="tool_error",
        note="example",
        agent="pytest",
    )

    assert "error" not in result
    assert captured["entity_id"] == "service:agent-bus"


@pytest.mark.parametrize("category", ["doc_drift", "protocol"])
def test_op_friction_accepts_expanded_categories(monkeypatch, category: str) -> None:
    captured: dict[str, object] = {}

    def fake_create(body: dict[str, object]) -> dict[str, object]:
        captured.update(body)
        return {"item": {"id": 1}}

    monkeypatch.setattr(
        "cortex_store.dispatch_ops.ops_assertions_write._create_assertion_impl",
        fake_create,
    )
    monkeypatch.setattr(
        "cortex_store.dispatch_ops.ops_assertions_write.record",
        lambda *a, **k: None,
    )

    result = _op_friction(
        service="mcp-server",
        category=category,
        note="session-close taxonomy",
        agent="pytest",
    )

    assert "error" not in result
    assert captured["claim"] == f"[{category}] session-close taxonomy"
