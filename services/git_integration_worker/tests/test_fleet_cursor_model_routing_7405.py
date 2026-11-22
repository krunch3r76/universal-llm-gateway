"""Fleet cursor-model routing — omit-path default regression tests (7405)."""

from __future__ import annotations

from cursor_capabilities import default_variant, supported_knobs

from services.git_integration_worker.cursor_models import (
    build_model_selection,
    resolve_cursor,
)


def test_grok_omit_path_fast_true() -> None:
    cfg = resolve_cursor("grok-4.7")
    selection = build_model_selection(cfg)
    emitted = {p.id: p.value for p in selection.params}
    assert emitted["fast"] == "true"
    assert default_variant("grok-4.7")["fast"] == "true"
    assert supported_knobs("grok-4.7")["fast"].default == "true"


def test_anthropic_omit_path_thinking_context_defaults() -> None:
    for model in (
        "claude-opus-5",
        "claude-opus-4-8",
        "claude-sonnet-5",
        "claude-fable-5",
    ):
        cfg = resolve_cursor(model)
        selection = build_model_selection(cfg)
        emitted = {p.id: p.value for p in selection.params}
        assert emitted["thinking"] == "true", model
        assert emitted["context"] == "1m", model


def test_gpt_omit_path_context_272k() -> None:
    for model in ("gpt-5.5", "gpt-5.6-sol", "gpt-5.6-terra", "gpt-5.6-luna"):
        cfg = resolve_cursor(model)
        selection = build_model_selection(cfg)
        emitted = {p.id: p.value for p in selection.params}
        assert emitted["context"] == "272k", model
        assert default_variant(model)["context"] == "272k"


















