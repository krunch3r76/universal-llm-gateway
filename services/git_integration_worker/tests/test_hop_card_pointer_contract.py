"""G2 hop-card pointer emission contract (todo:hop-card-pointer-contract)."""

from __future__ import annotations

from pathlib import Path

import pytest

from services.git_integration_worker.cursor_auto.hop_cadence import (
    build_cadence_hop_body,
)
from services.git_integration_worker.cursor_auto.hop_cadence_watch import (
    HopDecision,
    StandingHandoffFreshness,
)
from services.git_integration_worker.cursor_auto.l2_orientation import (
    extract_lane_tip,
    read_cse_state,
    render_arrival_card,
)

pytestmark = pytest.mark.offline

_THREAD = "pointer-contract-test"
_STANDING_URI = f"cortex://notes/system/threads/{_THREAD}-standing-handoff.md"
_RULES_URI = f"cortex://notes/system/threads/{_THREAD}-arrival-card.md"
_READ_URI = "Read the standing handoff URI above before trusting any wake prose."


def _turn(*, n: int, body: str) -> dict:
    return {
        "id": 1000 + n,
        "turn_number": n,
        "from": "seat-a",
        "subject": "test",
        "body": body,
        "created_at": "2026-08-12T16:00:00Z",
    }


def _handoff(status: str) -> StandingHandoffFreshness:
    return StandingHandoffFreshness(
        status=status,
        uri=_STANDING_URI,
        mtime_epoch=None if status == "missing" else 1.0,
        age_s=None if status == "missing" else 10.0,
    )


def _card(*, rules_exist: bool, status: str = "current") -> str:
    cse = read_cse_state(thread_id=_THREAD)
    tip = extract_lane_tip(thread_id=_THREAD, turns=[_turn(n=1, body="hello")])
    card, _dropped = render_arrival_card(
        thread_id=_THREAD,
        generated_at="2026-08-12T16:00:00Z",
        cse=cse,
        tip=tip,
        obligations=[],
        admit_bind=None,
        rules_card_exists=rules_exist,
        standing_handoff=_handoff(status),
    )
    return card


def _decision(status: str) -> HopDecision:
    return HopDecision(
        thread_id=_THREAD,
        action="fire",
        reason="age_threshold_met",
        age_s=2000.0,
        threshold_s=1500.0,
        signal="watch_seated_at",
        handoff=_handoff(status),
    )


def test_arrival_card_emits_manual_rules_when_file_exists() -> None:
    card = _card(rules_exist=True)
    assert f"- Manual rules card: {_RULES_URI}" in card
    assert "## Durable rules (abbreviated — full set in manual card)" in card


def test_arrival_card_omits_manual_rules_when_file_absent() -> None:
    card = _card(rules_exist=False)
    assert "Manual rules card:" not in card
    assert _RULES_URI not in card
    assert "## Durable rules (abbreviated — full set in manual card)" not in card
    assert "## Durable rules (abbreviated)" in card


@pytest.mark.parametrize("status", ["current", "stale", "missing"])
def test_arrival_card_pointers_render_freshness_token(status: str) -> None:
    card = _card(rules_exist=False, status=status)
    adjudication = [
        line for line in card.splitlines() if line.startswith("- Adjudication only:")
    ]
    assert adjudication == [
        f"- Adjudication only: {_STANDING_URI} (standing_handoff_freshness: {status})"
    ]


@pytest.mark.parametrize("status", ["current", "stale", "missing"])
def test_cadence_hop_body_renders_freshness_token(status: str) -> None:
    body = build_cadence_hop_body(_decision(status))
    assert f"standing_handoff_freshness: {status}" in body.splitlines()


def test_cadence_hop_body_read_uri_when_not_missing() -> None:
    for status in ("current", "stale"):
        body = build_cadence_hop_body(_decision(status))
        assert _READ_URI in body
        assert "The S7 standing-handoff state file is absent." not in body


def test_cadence_hop_body_missing_instructs_author_not_read() -> None:
    body = build_cadence_hop_body(_decision("missing"))
    assert _READ_URI not in body
    assert "The S7 standing-handoff state file is absent." in body
    assert "Lane-tip reconstruction is degraded, not equivalent." in body
    assert "Author the standing handoff before you leave." in body


def test_cadence_adapter_equals_shared_author() -> None:
    """GIW adapter must not drift from hop_handoff.build_continuity_handoff_body."""
    from hop_handoff import StandingHandoffFreshness, build_continuity_handoff_body

    decision = _decision("current")
    via_adapter = build_cadence_hop_body(decision)
    via_lib = build_continuity_handoff_body(
        thread_id=decision.thread_id,
        trigger=decision.signal or "watch_seated_at",
        source="cursor-auto-hop-cadence",
        handoff=StandingHandoffFreshness(
            status="current",
            uri=_STANDING_URI,
            mtime_epoch=1.0,
            age_s=10.0,
        ),
        age_s=decision.age_s,
        threshold_s=decision.threshold_s,
    )
    assert via_adapter == via_lib


def test_emission_does_not_write_static_sidecars(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    card_path = tmp_path / f"{_THREAD}-arrival-card.md"
    handoff_path = tmp_path / f"{_THREAD}-standing-handoff.md"
    import services.git_integration_worker.cursor_auto.l2_orientation as l2_mod

    monkeypatch.setattr(l2_mod, "arrival_card_path", lambda tid: card_path)
    monkeypatch.setattr(
        l2_mod,
        "assess_standing_handoff",
        lambda tid, **kw: _handoff("missing"),
    )
    cse = read_cse_state(thread_id=_THREAD)
    tip = extract_lane_tip(thread_id=_THREAD, turns=[_turn(n=1, body="hello")])
    render_arrival_card(
        thread_id=_THREAD,
        generated_at="2026-08-12T16:00:00Z",
        cse=cse,
        tip=tip,
        obligations=[],
        admit_bind=None,
    )
    build_cadence_hop_body(_decision("missing"))
    assert not card_path.exists()
    assert not handoff_path.exists()
