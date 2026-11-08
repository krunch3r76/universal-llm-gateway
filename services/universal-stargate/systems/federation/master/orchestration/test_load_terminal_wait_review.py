"""Review H1/B3 regressions for load_terminal_wait."""

from __future__ import annotations

from model_id import ModelId

from systems.federation.master.orchestration.load_terminal_wait import (
    _FederationProgressThrottle,
    _model_matches,
)


def test_model_matches_uses_parsed_equality_not_prefix() -> None:
    wait_for = ModelId.parse("qwen3-32b-awq-16384")
    assert _model_matches({"model_id": "qwen3"}, wait_for) is False
    assert _model_matches({"model_id": str(wait_for)}, wait_for) is True


def test_federation_progress_throttle_skips_small_pct_bumps() -> None:
    throttle = _FederationProgressThrottle()
    now = 1000.0
    assert throttle.should_emit(phase="load", pct=10.0, now_mono=now) is True
    throttle.record_emit(phase="load", pct=10.0, now_mono=now)
    assert throttle.should_emit(phase="load", pct=12.0, now_mono=now + 1.0) is False
    assert throttle.should_emit(phase="load", pct=16.0, now_mono=now + 2.0) is True
    assert throttle.should_emit(phase="tokens", pct=16.0, now_mono=now + 3.0) is True
