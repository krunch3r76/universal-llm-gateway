"""Tests for assertion list filter SQL builders and frictions dispatch op."""

from __future__ import annotations

from cortex_store.dispatch_ops.ops_assertions import _op_frictions
from cortex_store.routes.assertions._list_filters import (
    _like_prefix_pattern,
    _like_substring_pattern,
    append_assertion_list_filters,
)


def test_like_patterns_escape_wildcards() -> None:
    assert _like_substring_pattern("tool_error") == "%tool\\_error%"
    assert _like_prefix_pattern("service:") == "service:%"


def test_append_filters_entity_id_prefix_and_claim() -> None:
    clauses: list[str] = []
    params: list[str | int] = []
    needs_join = append_assertion_list_filters(
        clauses,
        params,
        entity_id_prefix="service:",
        claim_filter="[tool_error]",
        seeded_by="claude-web",
        superseded=False,
    )
    assert needs_join is False
    assert "a.entity_id LIKE ? ESCAPE '\\'" in clauses
    assert "a.claim LIKE ? ESCAPE '\\'" in clauses
    assert "a.seeded_by = ?" in clauses
    assert "a.superseded_by IS NULL" in clauses
    assert params == ["service:%", "%[tool\\_error]%", "claude-web"]


def test_entity_id_wins_over_prefix() -> None:
    clauses: list[str] = []
    params: list[str | int] = []
    append_assertion_list_filters(
        clauses,
        params,
        entity_id="service:mcp-server",
        entity_id_prefix="service:",
    )
    assert clauses == ["a.entity_id = ?"]
    assert params == ["service:mcp-server"]


def test_op_frictions_defaults(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def fake_list(**kwargs: object) -> dict[str, object]:
        captured.update(kwargs)
        return {"items": []}

    monkeypatch.setattr(
        "cortex_store.dispatch_ops.ops_assertions._list_assertions_impl",
        fake_list,
    )
    result = _op_frictions(category="tool_error", seeded_by="claude-web")
    assert captured["entity_id_prefix"] == "service:"
    assert captured["claim_filter"] == "[tool_error]"
    assert captured["seeded_by"] == "claude-web"
    assert captured["superseded"] is False
    assert "_next" in result


def test_op_frictions_scoped_service(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def fake_list(**kwargs: object) -> dict[str, object]:
        captured.update(kwargs)
        return {"items": []}

    monkeypatch.setattr(
        "cortex_store.dispatch_ops.ops_assertions._list_assertions_impl",
        fake_list,
    )
    _op_frictions(service="mcp-server")
    assert captured["entity_id"] == "service:mcp-server"
    assert captured.get("entity_id_prefix") is None
