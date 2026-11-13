"""Unit tests for Grok roster paste gate (a:43821 isRunning slice)."""

from __future__ import annotations

import time

import pytest
from cursor_bridge.grok_roster_paste_gate import (
    GrokRosterGateSample,
    evaluate_grok_roster_gate,
    reset_grok_roster_gate_trackers,
)


@pytest.fixture(autouse=True)
def _reset_trackers() -> None:
    reset_grok_roster_gate_trackers()
    yield
    reset_grok_roster_gate_trackers()


def _idle_sample(**extra: object) -> GrokRosterGateSample:
    return GrokRosterGateSample(
        ok=True,
        is_running=False,
        is_composing=False,
        composer_draft="",
        **extra,  # type: ignore[arg-type]
    )


def test_busy_then_idle_delivers_once(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GROK_ROSTER_GATE_STABLE_S", "0")
    busy = GrokRosterGateSample(
        ok=True,
        is_running=True,
        is_composing=False,
        composer_draft="",
    )
    allowed, reason, _detail = evaluate_grok_roster_gate(
        display="9", agent_id="a1", sample=busy, now=0.0
    )
    assert not allowed
    assert reason == "agent_running"
    allowed2, reason2, _ = evaluate_grok_roster_gate(
        display="9", agent_id="a1", sample=_idle_sample(), now=1.0
    )
    assert allowed2
    assert reason2 is None


def test_flapping_inside_stable_window_blocks_paste(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GROK_ROSTER_GATE_STABLE_S", "5")
    t0 = 100.0
    ok1, reason1, _ = evaluate_grok_roster_gate(
        display="9", agent_id="a1", sample=_idle_sample(), now=t0
    )
    assert not ok1
    assert reason1 == "grok_idle_unstable"
    busy = GrokRosterGateSample(
        ok=True,
        is_running=True,
        is_composing=False,
        composer_draft="",
    )
    ok2, reason2, _ = evaluate_grok_roster_gate(
        display="9", agent_id="a1", sample=busy, now=t0 + 2.0
    )
    assert not ok2
    assert reason2 == "agent_running"
    ok3, reason3, _ = evaluate_grok_roster_gate(
        display="9", agent_id="a1", sample=_idle_sample(), now=t0 + 3.0
    )
    assert not ok3
    assert reason3 == "grok_idle_unstable"


def test_recheck_catches_busy_flip() -> None:
    sample = GrokRosterGateSample(
        ok=True,
        is_running=False,
        is_composing=True,
        composer_draft="",
    )
    allowed, reason, detail = evaluate_grok_roster_gate(
        display="9", agent_id="a1", sample=sample, now=0.0
    )
    assert not allowed
    assert reason == "grok_composing"
    assert detail.get("release_claim_for_retry") is True


def test_missing_roster_fields_defer() -> None:
    sample = GrokRosterGateSample.from_cdp(
        {"ok": True, "isRunning": False},
        composer_draft="",
    )
    allowed, reason, detail = evaluate_grok_roster_gate(
        display="9", agent_id="a1", sample=sample, now=0.0
    )
    assert not allowed
    assert reason == "grok_roster_unreadable"
    assert detail.get("release_claim_for_retry") is True


def test_stable_window_elapsed_allows(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GROK_ROSTER_GATE_STABLE_S", "5")
    t0 = time.monotonic()
    evaluate_grok_roster_gate(display="9", agent_id="a1", sample=_idle_sample(), now=t0)
    allowed, reason, _ = evaluate_grok_roster_gate(
        display="9", agent_id="a1", sample=_idle_sample(), now=t0 + 5.0
    )
    assert allowed
    assert reason is None
