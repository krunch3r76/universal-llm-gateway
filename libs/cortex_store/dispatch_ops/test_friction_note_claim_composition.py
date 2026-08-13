"""Friction note/claim alias composition — no silent discard (agent-bus:7188)."""

from __future__ import annotations

from pathlib import Path

import pytest

from cortex_store.dispatch_ops.ops_assertions_friction import _op_friction
from cortex_store.dispatch_ops.ops_assertions_update import _op_assertion_get

pytestmark = pytest.mark.offline


def _seed_service(conn) -> None:  # type: ignore[no-untyped-def]
    conn.execute(
        "INSERT OR IGNORE INTO entities (id, type, name) VALUES (?, 'service', ?)",
        ("service:friction-alias-probe", "friction-alias-probe"),
    )
    conn.commit()


def test_friction_conflicting_note_and_claim_returns_422_without_write(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    create_calls: list[dict[str, object]] = []

    def fake_create(body: dict[str, object]) -> dict[str, object]:
        create_calls.append(body)
        return {"item": {"id": 1}}

    monkeypatch.setattr(
        "cortex_store.dispatch_ops.ops_assertions_friction._create_assertion_impl",
        fake_create,
    )

    result = _op_friction(
        owner="service:friction-alias-probe",
        note="wrapper note kept by old code",
        claim="substantive finding that must not be silently dropped",
        category="tool_error",
        agent="pytest",
    )

    assert result.get("status_code") == 422
    err = result["error"]
    if isinstance(err, dict) and "errors" in err:
        fields = {entry["field"] for entry in err["errors"]}
        assert "note" in fields
    else:
        assert err["field"] == "note"
    assert create_calls == []


def test_friction_claim_only_persists_in_stored_row(
    migrated_db_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from cortex_store import db as cortex_db

    monkeypatch.setattr(cortex_db, "_CORTEX_DB", migrated_db_path)
    with cortex_db.cortex_conn() as conn:
        _seed_service(conn)

    body_text = "enum membership is not a valid liveness probe for request-surface verbs"
    result = _op_friction(
        owner="service:friction-alias-probe",
        claim=body_text,
        category="tool_error",
        suggestion="Invoke the verb; do not trust enum diff alone",
        agent="pytest",
    )
    assert "error" not in result, result
    assertion_id = int((result.get("item") or {})["id"])

    stored = _op_assertion_get(assertion_id=assertion_id)
    item = stored.get("item") or stored
    assert item["claim"] == (
        "[tool_error] enum membership is not a valid liveness probe for "
        "request-surface verbs — Suggestion: Invoke the verb; do not trust enum diff alone"
    )


def test_friction_empty_note_uses_claim_alias(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
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
        owner="service:mcp-server",
        note="",
        claim="claim-only via empty note string",
        category="schema_gap",
        agent="pytest",
    )
    assert "error" not in result, result
    assert captured["claim"] == "[schema_gap] claim-only via empty note string"
