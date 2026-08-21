"""Tests for friction/frictions service slug normalization (friction 16851)."""

from __future__ import annotations

import json

import pytest

from cortex_store.dispatch_ops import execute_op
from cortex_store.dispatch_ops._shared import (
    _FRICTION_CATEGORIES,
    normalize_service_slug,
    owner_entity_id,
    owner_type_of,
    service_entity_id,
)
from cortex_store.dispatch_ops.ops_assertions import _op_frictions
from cortex_store.dispatch_ops.ops_assertions_write import _op_friction


def test_friction_categories_include_regression() -> None:
    assert "regression" in _FRICTION_CATEGORIES


def test_friction_categories_include_feature() -> None:
    assert "feature" in _FRICTION_CATEGORIES


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
        "cortex_store.dispatch_ops.ops_assertions_friction._list_assertions_impl",
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
        "cortex_store.dispatch_ops.ops_assertions_friction._create_assertion_impl",
        fake_create,
    )
    monkeypatch.setattr(
        "cortex_store.dispatch_ops.ops_assertions_friction.record",
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


@pytest.mark.parametrize("category", ["doc_drift", "protocol", "regression", "feature"])
def test_op_friction_accepts_expanded_categories(monkeypatch, category: str) -> None:
    captured: dict[str, object] = {}

    def fake_create(body: dict[str, object]) -> dict[str, object]:
        captured.update(body)
        return {"item": {"id": 1}}

    monkeypatch.setattr(
        "cortex_store.dispatch_ops.ops_assertions_friction._create_assertion_impl",
        fake_create,
    )
    monkeypatch.setattr(
        "cortex_store.dispatch_ops.ops_assertions_friction.record",
        lambda *a, **k: None,
    )

    extra: dict[str, object] = {}
    if category == "protocol":
        extra = {"charter_root": "1", "window_index": 1, "actionable": True}
    result = _op_friction(
        service="mcp-server",
        category=category,
        note="session-close taxonomy",
        agent="pytest",
        **extra,
    )

    assert "error" not in result
    assert captured["claim"] == f"[{category}] session-close taxonomy"


def test_execute_op_friction_json_string_routes_service(monkeypatch) -> None:
    """Dispatch boundary: execute_op('friction', JSON-string) must parse the
    JSON-string ``arguments`` and route ``service`` through to _op_friction
    intact.

    Regression guard for superseded assertion 17239 (friction reported as
    'service is required' on web seats). The failure boundary asserted there
    was the dispatch path (execute_op -> _parse_cortex_arguments -> handler),
    which the direct-handler tests above never exercise.
    """
    captured: dict[str, object] = {}

    def fake_create(body: dict[str, object]) -> dict[str, object]:
        captured.update(body)
        return {"item": {"id": 1}}

    monkeypatch.setattr(
        "cortex_store.dispatch_ops.ops_assertions_friction._create_assertion_impl",
        fake_create,
    )
    monkeypatch.setattr(
        "cortex_store.dispatch_ops.ops_assertions_friction.record",
        lambda *a, **k: None,
    )
    monkeypatch.setattr(
        "cortex_store.dispatch_ops.record",
        lambda *a, **k: None,
    )

    payload = {
        "service": "mcp-server",
        "category": "tool_absent",
        "agent": "pytest",
        "note": "dispatch-boundary regression probe",
    }
    result = execute_op("friction", json.dumps(payload))

    assert "error" not in result
    assert captured["entity_id"] == "service:mcp-server"


def test_execute_op_friction_invalid_category_is_nonwriting(monkeypatch) -> None:
    """Dispatch boundary, non-writing: an invalid category must error with
    'Invalid category ...' (proving ``service`` reached _op_friction and passed
    the ``if not service`` guard) and must NOT reach the assertion write.

    Mirrors the live non-writing invalid-category probe used to falsify
    assertion 17239 across both caller paths.
    """
    create_calls: list[dict[str, object]] = []

    def fake_create(body: dict[str, object]) -> dict[str, object]:
        create_calls.append(body)
        return {"item": {"id": 1}}

    monkeypatch.setattr(
        "cortex_store.dispatch_ops.ops_assertions_friction._create_assertion_impl",
        fake_create,
    )
    monkeypatch.setattr(
        "cortex_store.dispatch_ops.ops_assertions_friction.record",
        lambda *a, **k: None,
    )
    monkeypatch.setattr(
        "cortex_store.dispatch_ops.record",
        lambda *a, **k: None,
    )

    payload = {
        "service": "mcp-server",
        "category": "__probe_invalid_category__",
        "agent": "pytest",
        "note": "non-writing invalid-category dispatch probe",
    }
    result = execute_op("friction", json.dumps(payload))

    assert "error" in result
    err = result["error"]
    message = err.get("message", err) if isinstance(err, dict) else err
    assert str(message).startswith("Invalid category")
    assert "owner is required" not in str(err)
    assert create_calls == []


def test_owner_entity_id_helpers() -> None:
    assert owner_entity_id("mcp-server") == "service:mcp-server"
    assert owner_entity_id("service:mcp-server") == "service:mcp-server"
    assert (
        owner_entity_id("agent_skill:friction-review") == "agent_skill:friction-review"
    )
    assert owner_type_of("service:mcp-server") == "service"
    assert owner_type_of("agent_skill:x") == "agent_skill"
    assert owner_type_of("decision:foo") is None


def test_op_friction_owner_bare_slug(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def fake_create(body: dict[str, object]) -> dict[str, object]:
        captured.update(body)
        return {"item": {"id": 1}}

    monkeypatch.setattr(
        "cortex_store.dispatch_ops.ops_assertions_friction._create_assertion_impl",
        fake_create,
    )
    monkeypatch.setattr(
        "cortex_store.dispatch_ops.ops_assertions_friction.record",
        lambda *a, **k: None,
    )

    result = _op_friction(
        owner="mcp-server",
        category="lesson_gap",
        note="legacy owner bare slug",
        agent="pytest",
    )
    assert "error" not in result
    assert captured["entity_id"] == "service:mcp-server"


def test_op_friction_agent_skill_owner(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def fake_create(body: dict[str, object]) -> dict[str, object]:
        captured.update(body)
        return {"item": {"id": 1}}

    class _Resolved:
        entity_id = "agent_skill:friction-review"

    monkeypatch.setattr(
        "cortex_store.dispatch_ops.ops_assertions_friction._create_assertion_impl",
        fake_create,
    )
    monkeypatch.setattr(
        "cortex_store.dispatch_ops.ops_assertions_friction.record",
        lambda *a, **k: None,
    )
    monkeypatch.setattr(
        "cortex_store.dispatch_ops.ops_assertions_friction.resolve_entity_reference",
        lambda conn, eid, **kw: _Resolved(),
    )

    result = _op_friction(
        owner="agent_skill:friction-review",
        category="lesson_gap",
        note="conduct lesson on skill",
        agent="pytest",
    )
    assert "error" not in result
    assert captured["entity_id"] == "agent_skill:friction-review"


def test_op_friction_missing_agent_skill_owner_no_write(monkeypatch) -> None:
    create_calls: list[dict[str, object]] = []

    def fake_create(body: dict[str, object]) -> dict[str, object]:
        create_calls.append(body)
        return {"item": {"id": 1}}

    def fake_resolve(conn: object, eid: str, **kw: object) -> object:
        from fastapi import HTTPException

        raise HTTPException(status_code=404, detail="entity not found")

    monkeypatch.setattr(
        "cortex_store.dispatch_ops.ops_assertions_friction._create_assertion_impl",
        fake_create,
    )
    monkeypatch.setattr(
        "cortex_store.dispatch_ops.ops_assertions_friction.resolve_entity_reference",
        fake_resolve,
    )

    result = _op_friction(
        owner="agent_skill:does-not-exist",
        category="lesson_gap",
        note="typo guard probe",
        agent="pytest",
    )
    assert "error" in result
    assert "not found" in result["error"]
    assert create_calls == []


def test_op_friction_both_owner_service_unequal_errors() -> None:
    result = _op_friction(
        owner="agent_skill:friction-review",
        service="mcp-server",
        category="lesson_gap",
        note="precedence probe",
        agent="pytest",
    )
    assert "error" in result
    assert "not both with different values" in result["error"]


def test_op_friction_both_owner_service_equal_ok(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def fake_create(body: dict[str, object]) -> dict[str, object]:
        captured.update(body)
        return {"item": {"id": 1}}

    monkeypatch.setattr(
        "cortex_store.dispatch_ops.ops_assertions_friction._create_assertion_impl",
        fake_create,
    )
    monkeypatch.setattr(
        "cortex_store.dispatch_ops.ops_assertions_friction.record",
        lambda *a, **k: None,
    )

    result = _op_friction(
        owner="mcp-server",
        service="mcp-server",
        category="lesson_gap",
        note="equal alias ok",
        agent="pytest",
    )
    assert "error" not in result
    assert captured["entity_id"] == "service:mcp-server"


def test_op_friction_unsupported_namespace() -> None:
    result = _op_friction(
        owner="decision:foo",
        category="lesson_gap",
        note="namespace guard probe",
        agent="pytest",
    )
    assert "error" in result
    assert "Unsupported owner namespace" in result["error"]


def _patch_friction_create(monkeypatch, captured: dict[str, object]) -> None:
    def fake_create(body: dict[str, object]) -> dict[str, object]:
        captured.update(body)
        return {"item": {"id": 1}}

    monkeypatch.setattr(
        "cortex_store.dispatch_ops.ops_assertions_friction._create_assertion_impl",
        fake_create,
    )
    monkeypatch.setattr(
        "cortex_store.dispatch_ops.ops_assertions_friction.record",
        lambda *a, **k: None,
    )


def test_op_friction_feature_defaults_not_actionable(monkeypatch) -> None:
    captured: dict[str, object] = {}
    _patch_friction_create(monkeypatch, captured)

    result = _op_friction(
        service="git_integration_worker",
        category="feature",
        note="inherit-inhibit for new hop-cadence enrolls",
        agent="pytest",
        charter_root="480",
        window_index=1,
    )

    assert "error" not in result
    attrs = captured["attributes"]
    assert attrs["actionable"] is False
    assert attrs["defer_enqueue"] is True
    assert "observation only" in attrs["actionable_false_reason"]


def test_op_friction_feature_explicit_actionable_is_honoured(monkeypatch) -> None:
    captured: dict[str, object] = {}
    _patch_friction_create(monkeypatch, captured)

    result = _op_friction(
        service="git_integration_worker",
        category="feature",
        note="commissioned feature",
        agent="pytest",
        actionable=True,
    )

    assert "error" not in result
    attrs = captured.get("attributes") or {}
    assert attrs.get("actionable") is True
    assert "defer_enqueue" not in attrs
