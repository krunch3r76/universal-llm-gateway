"""Tests for ``cursor_models`` registry and knob validation."""

from __future__ import annotations

import pytest

from services.git_integration_worker.cursor_models import (
    build_model_selection,
    resolve_cursor,
    validate_knobs,
)


def test_resolve_cursor_bare_hit() -> None:
    cfg = resolve_cursor("composer-2.5")
    assert cfg.model_id == "composer-2.5"
    assert len(cfg.params) == 1


def test_resolve_cursor_prefixed_hit() -> None:
    cfg = resolve_cursor("cursor/composer-2.5")
    assert cfg.model_id == "composer-2.5"


def test_resolve_cursor_miss() -> None:
    with pytest.raises(ValueError, match="not in trusted allowlist"):
        resolve_cursor("cursor/unknown-model")


def test_resolve_cursor_wrong_provider_reject() -> None:
    with pytest.raises(ValueError, match="provider 'anthropic'"):
        resolve_cursor("anthropic/claude-opus-4-8")


def test_build_model_selection_default_omit() -> None:
    cfg = resolve_cursor("claude-opus-4-8")
    selection = build_model_selection(cfg)
    assert selection.id == "claude-opus-4-8"
    emitted = {p.id: p.value for p in selection.params}
    assert emitted == {"fast": "false"}
    assert "thinking" not in emitted


def test_build_model_selection_override() -> None:
    cfg = resolve_cursor("composer-2.5")
    selection = build_model_selection(cfg, {"fast": "true"})
    assert len(selection.params) == 1
    assert selection.params[0].value == "true"


def test_validate_knobs_collect_all() -> None:
    cfg = resolve_cursor("composer-2.5")
    with pytest.raises(ValueError, match="unknown knob 'bogus'"):
        validate_knobs(cfg, {"bogus": "x", "fast": "maybe"})
    with pytest.raises(ValueError, match="not in"):
        validate_knobs(cfg, {"fast": "maybe"})
