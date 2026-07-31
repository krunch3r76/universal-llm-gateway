"""Unit tests for ``TerminationShadowDetector``."""

from __future__ import annotations

from frontier_observability import TerminationShadowDetector
from frontier_observability.termination_shadow import (
    DETECTOR_MODE,
    DETECTOR_VERSION,
)


def _detect(**overrides: object) -> object:
    base: dict[str, object] = {
        "provider": "google",
        "boot_level": "team",
        "reasoning": {"text": ""},
        "content": "",
        "finish_reason": "STOP",
        "output_tokens": 10,
    }
    base.update(overrides)
    return TerminationShadowDetector().detect(**base)  # type: ignore[arg-type]


def test_returns_none_when_provider_not_google() -> None:
    thought = "I cannot help with that request."
    assert _detect(provider="anthropic", reasoning={"text": thought}) is None
    assert _detect(provider="openai", reasoning={"text": thought}) is None


def test_returns_none_when_boot_level_outside_team_full() -> None:
    thought = "I cannot help with that request."
    assert _detect(boot_level="none", reasoning={"text": thought}) is None
    assert _detect(boot_level="mcp", reasoning={"text": thought}) is None


def test_returns_none_when_reasoning_missing() -> None:
    assert _detect(reasoning=None) is None
    assert _detect(reasoning={"text": None}) is None
    assert _detect(reasoning={}) is None
    assert _detect(reasoning={"text": "   "}) is None


def test_detects_refusal_phrase_at_leading_position() -> None:
    thought = "I cannot assist with that. The user asked for something outside policy."
    payload = _detect(reasoning={"text": thought}, content="")
    assert payload is not None
    assert payload.reason == "refusal"
    assert payload.suggested_next_action == "escalate_to_user"
    assert payload.trace_visibility == "partial"
    # leading match adds a "position" evidence entry in addition to the phrase
    kinds = {e.kind for e in payload.evidence}
    assert "phrase" in kinds
    assert "position" in kinds
    # confidence bumped above bare phrase score since >1 evidence
    assert payload.confidence > 0.55


def test_suppresses_meta_discussion_via_counter_phrase() -> None:
    thought = (
        "For example, a typical refusal would be 'I cannot help'. "
        "However, the actual task is fine and I will proceed."
    )
    assert _detect(reasoning={"text": thought}, content="answer") is None


def test_detects_loop_via_ngram_repetition() -> None:
    repeated = "the user asked me to " * 6
    thought = "Thinking about the task. " + repeated
    payload = _detect(reasoning={"text": thought}, content="short")
    assert payload is not None
    assert payload.reason == "loop"
    assert any(e.kind == "repetition" for e in payload.evidence)


def test_detects_token_exhaustion_on_max_tokens_with_short_content() -> None:
    # Varied thought text so n-gram repetition does not fire — we want to
    # isolate the token_budget gate (MAX_TOKENS + short content).
    thought = (
        "First examining the question. Next considering the evidence. "
        "Then weighing alternatives against known constraints here. "
        "Finally drafting a preliminary response for output writing."
    )
    payload = _detect(
        reasoning={"text": thought},
        content="x",
        finish_reason="MAX_TOKENS",
    )
    assert payload is not None
    assert payload.reason == "token_exhaustion"
    assert any(e.kind == "token_budget" for e in payload.evidence)
    assert payload.suggested_next_action == "retry_with_context"


def test_accepts_string_reasoning_not_just_dict() -> None:
    thought = "I cannot help with that request."
    payload = _detect(reasoning=thought, content="")
    assert payload is not None
    assert payload.reason == "refusal"


def test_payload_has_generate_id_and_detector_descriptor() -> None:
    thought = "I cannot help here."
    payload = _detect(reasoning={"text": thought}, content="")
    assert payload is not None
    assert isinstance(payload.generate_id, str) and len(payload.generate_id) > 0
    assert payload.detector["version"] == DETECTOR_VERSION
    assert payload.detector["mode"] == DETECTOR_MODE
    assert payload.detector["provider"] == "google"


def test_as_dict_round_trip_preserves_evidence() -> None:
    thought = "I cannot help here."
    payload = _detect(reasoning={"text": thought}, content="")
    assert payload is not None
    d = payload.as_dict()
    assert d["reason"] == "refusal"
    assert isinstance(d["evidence"], list) and len(d["evidence"]) >= 1
    assert d["evidence"][0]["kind"] == "phrase"
