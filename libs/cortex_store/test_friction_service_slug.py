"""Tests for friction/frictions service slug normalization (friction 16851)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from cortex_store.dispatch_ops import execute_op
from cortex_store.dispatch_ops._shared import (
    _FRICTION_CATEGORIES,
    normalize_service_slug,
    owner_entity_id,
    owner_type_of,
    service_entity_id,
)

_REPO = Path(__file__).resolve().parents[2]
_CANONICAL = _REPO / "config" / "mcp" / "canonical.yaml"
from cortex_store.dispatch_ops.ops_assertions import _op_frictions
from cortex_store.dispatch_ops.ops_assertions_write import _op_friction


def test_canonical_friction_category_enum_matches_runtime() -> None:
    """MCP canonical.yaml category enum must match cortex_store _FRICTION_CATEGORIES."""
    raw = yaml.safe_load(_CANONICAL.read_text())
    friction = next(
        item
        for item in raw.get("tools", [])
        if item.get("canonical_name") == "cortex_friction"
    )
    schema_enum = set(
        friction["json_schema"]["properties"]["category"]["enum"]
    )
    assert schema_enum == set(_FRICTION_CATEGORIES)


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
    monkeypatch.setattr(
        "cortex_store.dispatch_ops.ops_assertions_friction._resolve_friction_owner_entity_id",
        lambda eid: eid,
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
    monkeypatch.setattr(
        "cortex_store.dispatch_ops.ops_assertions_friction._resolve_friction_owner_entity_id",
        lambda eid: eid,
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
    monkeypatch.setattr(
        "cortex_store.dispatch_ops.ops_assertions_friction._resolve_friction_owner_entity_id",
        lambda eid: eid,
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
    monkeypatch.setattr(
        "cortex_store.dispatch_ops.ops_assertions_friction._resolve_friction_owner_entity_id",
        lambda eid: eid,
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
    monkeypatch.setattr(
        "cortex_store.dispatch_ops.ops_assertions_friction._resolve_friction_owner_entity_id",
        lambda eid: eid,
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
    # Service owners now resolve; identity-stub keeps hermetic create-path tests
    # from needing a migrated DB (underscore→hyphen covered separately).
    monkeypatch.setattr(
        "cortex_store.dispatch_ops.ops_assertions_friction._resolve_friction_owner_entity_id",
        lambda eid: eid,
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


def test_op_friction_underscore_service_resolves_to_hyphen(
    migrated_db_path, monkeypatch
) -> None:
    """a:37201 — tool/manage slug agent_bus must land on service:agent-bus."""
    from cortex_store import db as cortex_db
    from cortex_store.conftest import bind_cortex_db

    bind_cortex_db(monkeypatch, migrated_db_path)
    with cortex_db.cortex_conn() as conn:
        conn.execute(
            "INSERT INTO entities (id, type, name, lifecycle) VALUES (?, ?, ?, ?)",
            ("service:agent-bus", "service", "Agent Bus", "active"),
        )
        conn.commit()

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
        owner="service:agent_bus",
        category="tool_error",
        note="underscore owner alias probe",
        agent="pytest",
    )
    assert "error" not in result, result
    assert captured["entity_id"] == "service:agent-bus"


def test_op_friction_bare_underscore_slug_resolves(
    migrated_db_path, monkeypatch
) -> None:
    from cortex_store import db as cortex_db
    from cortex_store.conftest import bind_cortex_db

    bind_cortex_db(monkeypatch, migrated_db_path)
    with cortex_db.cortex_conn() as conn:
        conn.execute(
            "INSERT INTO entities (id, type, name, lifecycle) VALUES (?, ?, ?, ?)",
            ("service:agent-bus", "service", "Agent Bus", "active"),
        )
        conn.commit()

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
        service="agent_bus",
        category="tool_error",
        note="bare underscore slug",
        agent="pytest",
    )
    assert "error" not in result, result
    assert captured["entity_id"] == "service:agent-bus"


def test_op_friction_missing_underscore_service_steers_hyphen(
    migrated_db_path, monkeypatch
) -> None:
    """404-with-hint when neither underscore nor hyphen entity exists."""
    from cortex_store.conftest import bind_cortex_db

    bind_cortex_db(monkeypatch, migrated_db_path)
    create_calls: list[dict[str, object]] = []

    monkeypatch.setattr(
        "cortex_store.dispatch_ops.ops_assertions_friction._create_assertion_impl",
        lambda body: create_calls.append(body) or {"item": {"id": 1}},
    )

    result = _op_friction(
        owner="service:no_such_bus",
        category="tool_error",
        note="steer probe",
        agent="pytest",
    )
    assert "error" in result
    assert result["status_code"] == 404
    assert "service:no-such-bus" in result["error"]
    assert "hyphens" in result["error"]
    assert create_calls == []


def test_op_friction_underscore_canonical_service_exact(
    migrated_db_path, monkeypatch
) -> None:
    """a:37201 review A4 — underscore-canonical ids must not be hyphen-rewritten."""
    from cortex_store import db as cortex_db
    from cortex_store.conftest import bind_cortex_db

    bind_cortex_db(monkeypatch, migrated_db_path)
    with cortex_db.cortex_conn() as conn:
        conn.execute(
            "INSERT INTO entities (id, type, name, lifecycle) VALUES (?, ?, ?, ?)",
            (
                "service:git_integration_worker",
                "service",
                "Git Integration Worker",
                "active",
            ),
        )
        conn.commit()

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
        owner="service:git_integration_worker",
        category="feature",
        note="exact underscore canonical",
        agent="pytest",
        actionable=False,
        actionable_false_reason="unit probe",
        defer_enqueue=True,
    )
    assert "error" not in result, result
    assert captured["entity_id"] == "service:git_integration_worker"


def test_op_friction_ambiguous_alias_not_rewritten_as_404(monkeypatch) -> None:
    """a:37201 review A2 — 400 ambiguous must surface, not soft-hyphen into 404."""
    from fastapi import HTTPException

    calls: list[str] = []

    def fake_resolve(conn: object, eid: str, **kw: object) -> object:
        calls.append(eid)
        raise HTTPException(
            status_code=400,
            detail={"detail": f"Ambiguous owner entity alias: {eid}", "matches": []},
        )

    monkeypatch.setattr(
        "cortex_store.dispatch_ops.ops_assertions_friction.resolve_entity_reference",
        fake_resolve,
    )
    create_calls: list[dict[str, object]] = []
    monkeypatch.setattr(
        "cortex_store.dispatch_ops.ops_assertions_friction._create_assertion_impl",
        lambda body: create_calls.append(body) or {"item": {"id": 1}},
    )

    result = _op_friction(
        owner="service:agent_bus",
        category="tool_error",
        note="ambiguous alias probe",
        agent="pytest",
    )
    assert "error" in result
    assert result["status_code"] == 400
    assert "Ambiguous" in str(result["error"])
    assert calls == ["service:agent_bus"]
    assert create_calls == []


def test_op_frictions_resolves_underscore_owner(migrated_db_path, monkeypatch) -> None:
    """a:37201 review A3 — list path must resolve the same way as write."""
    from cortex_store import db as cortex_db
    from cortex_store.conftest import bind_cortex_db

    bind_cortex_db(monkeypatch, migrated_db_path)
    with cortex_db.cortex_conn() as conn:
        conn.execute(
            "INSERT INTO entities (id, type, name, lifecycle) VALUES (?, ?, ?, ?)",
            ("service:agent-bus", "service", "Agent Bus", "active"),
        )
        conn.commit()

    captured: dict[str, object] = {}

    def fake_list(**kwargs: object) -> dict[str, object]:
        captured.update(kwargs)
        return {"items": []}

    monkeypatch.setattr(
        "cortex_store.dispatch_ops.ops_assertions_friction._list_assertions_impl",
        fake_list,
    )
    result = _op_frictions(owner="service:agent_bus")
    assert "error" not in result, result
    assert captured["entity_id"] == "service:agent-bus"
