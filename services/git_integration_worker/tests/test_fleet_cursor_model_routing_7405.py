"""Fleet cursor-model routing — omit-path default regression tests (7405)."""

from __future__ import annotations

from contract_vocab import CANONICAL_CONTRACTS
from cursor_capabilities import default_variant, supported_knobs
from effort_vocabulary import AUTO_EFFORT

from services.git_integration_worker.cursor_auto.knob_compose import compose_model_knobs
from services.git_integration_worker.cursor_auto.wire_map import resolve_desired_effort
from services.git_integration_worker.cursor_models import (
    build_model_selection,
    resolve_cursor,
)


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
        "context": "300k",
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
    assert effort["requested"] == AUTO_EFFORT
    assert effort["clamped"] is False


def test_auto_sentinel_equals_omit() -> None:
    keys = tuple(CANONICAL_CONTRACTS) + ("unknown",)
    for contract in keys:
        baseline = resolve_desired_effort(None, contract=contract)
        assert resolve_desired_effort("auto", contract=contract) == baseline
        assert resolve_desired_effort("", contract=contract) == baseline
        assert resolve_desired_effort("AUTO", contract=contract) == baseline
        assert resolve_desired_effort(" auto ", contract=contract) == baseline


def test_omit_effort_light_bounded_contracts_xhigh() -> None:
    for contract in ("verify", "execute", "propagate"):
        effort = resolve_desired_effort(None, contract=contract)
        assert effort["resolved_effort"] == "xhigh"
        assert effort["requested"] == AUTO_EFFORT


def test_omit_effort_implement_mechanical_medium_judgment_xhigh() -> None:
    mechanical = resolve_desired_effort(None, contract="implement")
    assert mechanical["resolved_effort"] == "medium"
    judgment = resolve_desired_effort(
        None, contract="implement", handoff_contract="light-bounded"
    )
    assert judgment["resolved_effort"] == "xhigh"
    pure = resolve_desired_effort(
        None, contract="implement", handoff_contract="pure-mechanical"
    )
    assert pure["resolved_effort"] == "medium"


def test_explicit_medium_honored_on_judgment_contract() -> None:
    effort = resolve_desired_effort("medium", contract="investigate")
    assert effort["resolved_effort"] == "medium"
    assert effort["requested"] == "medium"
    assert effort["notes"] == "honored"


def test_compose_grok_investigate_omit_xhigh() -> None:
    knobs = compose_model_knobs(
        {"resolved_model_id": "cursor/grok-4.6"},
        resolve_desired_effort("auto", contract="investigate"),
        contract="investigate",
    )
    assert knobs == {"effort": "xhigh", "fast": "false"}


def test_compose_composer_omit_path_fast_true() -> None:
    """Implement omit ⇒ medium; Composer has no effort knob so knobs stay empty."""
    knobs = compose_model_knobs(
        {"resolved_model_id": "cursor/composer-2.5"},
        resolve_desired_effort(None, contract="implement"),
        contract="implement",
    )
    assert knobs == {}
