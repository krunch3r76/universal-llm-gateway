"""Fleet cursor-model routing — omit-path default regression tests (7405)."""

from __future__ import annotations

from cursor_capabilities import default_variant, supported_knobs

from services.git_integration_worker.cursor_auto.knob_compose import compose_model_knobs
from services.git_integration_worker.cursor_auto.wire_map import resolve_desired_effort
from services.git_integration_worker.cursor_models import build_model_selection, resolve_cursor


def test_grok_omit_path_fast_false() -> None:
    cfg = resolve_cursor("grok-4.6")
    selection = build_model_selection(cfg)
    emitted = {p.id: p.value for p in selection.params}
    assert emitted["fast"] == "false"
    assert default_variant("grok-4.6")["fast"] == "false"
    assert supported_knobs("grok-4.6")["fast"].default == "false"


def test_anthropic_omit_path_thinking_context_defaults() -> None:
    for model in ("claude-opus-5", "claude-opus-4-8", "claude-sonnet-5", "claude-fable-5"):
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


def test_compose_investigate_sonnet5_knobs() -> None:
    knobs = compose_model_knobs(
        {"resolved_model_id": "cursor/claude-sonnet-5"},
        resolve_desired_effort(None, contract="investigate"),
        contract="investigate",
    )
    assert knobs == {
        "effort": "xhigh",
        "thinking": "true",
        "context": "1m",
    }


def test_compose_confer_grok_xhigh_fast_false() -> None:
    knobs = compose_model_knobs(
        {"resolved_model_id": "cursor/grok-4.6"},
        resolve_desired_effort(None, contract="confer"),
        contract="confer",
    )
    assert knobs == {"effort": "xhigh", "fast": "false"}


def test_omit_effort_answer_stays_medium() -> None:
    effort = resolve_desired_effort(None, contract="answer")
    assert effort["resolved_effort"] == "medium"
