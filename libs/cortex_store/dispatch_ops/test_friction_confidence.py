"""friction() must honour confidence — never silently downgrade (a:26439 item 6)."""

from __future__ import annotations

from cortex_store.dispatch_ops.ops_assertions_friction import _op_friction


def _patch_create(monkeypatch, captured: dict[str, object]) -> None:
    def fake_create(body: dict[str, object]) -> dict[str, object]:
        captured.clear()
        captured.update(body)
        return {"item": {"id": 1, **body}}

    monkeypatch.setattr(
        "cortex_store.dispatch_ops.ops_assertions_friction._create_assertion_impl",
        fake_create,
    )
    monkeypatch.setattr(
        "cortex_store.dispatch_ops.ops_assertions_friction.record",
        lambda *a, **k: None,
    )


def test_friction_honours_confirmed_confidence(monkeypatch) -> None:
    captured: dict[str, object] = {}
    _patch_create(monkeypatch, captured)
    result = _op_friction(
        owner="service:mcp-server",
        category="lesson_gap",
        note="source-traced causal chain",
        agent="pytest",
        confidence="confirmed",
    )
    assert "error" not in result, result
    assert captured["confidence"] == "confirmed"
    assert captured["confidence_score"] == 1.0


def test_friction_default_confidence_unchanged(monkeypatch) -> None:
    captured: dict[str, object] = {}
    _patch_create(monkeypatch, captured)
    result = _op_friction(
        owner="service:mcp-server",
        category="lesson_gap",
        note="omitted confidence stays hypothesized/0.5",
        agent="pytest",
    )
    assert "error" not in result, result
    assert captured["confidence"] == "hypothesized"
    assert captured["confidence_score"] == 0.5


def test_friction_rejects_invalid_confidence(monkeypatch) -> None:
    create_calls: list[dict[str, object]] = []

    def fake_create(body: dict[str, object]) -> dict[str, object]:
        create_calls.append(body)
        return {"item": {"id": 1}}

    monkeypatch.setattr(
        "cortex_store.dispatch_ops.ops_assertions_friction._create_assertion_impl",
        fake_create,
    )
    result = _op_friction(
        owner="service:mcp-server",
        category="lesson_gap",
        note="bad confidence must not write",
        agent="pytest",
        confidence="certain",
    )
    assert "error" in result
    assert "Invalid confidence" in result["error"]
    assert create_calls == []


def test_friction_honours_explicit_confidence_score(monkeypatch) -> None:
    captured: dict[str, object] = {}
    _patch_create(monkeypatch, captured)
    result = _op_friction(
        owner="service:mcp-server",
        category="lesson_gap",
        note="explicit score wins",
        agent="pytest",
        confidence="believed",
        confidence_score=0.85,
    )
    assert "error" not in result, result
    assert captured["confidence"] == "believed"
    assert captured["confidence_score"] == 0.85
