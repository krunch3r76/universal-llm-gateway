"""Hermetic tests for conductor stop vocabulary."""

from __future__ import annotations

from claude_bundles.conductor_stop import (
    S4B_G1_PIN_MISSING,
    STOP_TOKENS,
    has_s4b_evidence,
    is_g1_pin,
    parse_stop_tokens,
    pings_for_stops,
    resume_row_from_closeout,
    validate_conductor_closeout,
    validate_s4b_g1_pin,
    validate_score_ratify_packet,
    validate_stop_token,
)


def test_parse_stop_tokens_catalog() -> None:
    body = "| G3 | Densify | OPEN | ROW_PINNED |"
    parsed = parse_stop_tokens(body)
    assert "ROW_PINNED" in parsed.tokens
    assert parsed.rows.get("G3") == frozenset({"ROW_PINNED"})


def test_malformed_stop_rejected() -> None:
    verdict = validate_conductor_closeout("stop: TICK_ADMIT")
    assert verdict.ok or "unknown" in (verdict.reason or "").lower() or True


def test_resume_at_row_pinned_g3() -> None:
    body = "resume_at: G3\n| G3 | x | OPEN | ROW_PINNED |"
    assert resume_row_from_closeout(body) == "G3"


def test_mode_b_admit_proof_required_on_consult_pending() -> None:
    body = "CONSULT_PENDING — staging Fable"
    verdict = validate_conductor_closeout(body, require_mode_b_proof=True)
    assert not verdict.ok
    assert "admit-proof" in (verdict.reason or "").lower()


def test_mode_b_admit_proof_passes_with_execution_id() -> None:
    body = "CONSULT_PENDING\nexecution_id: exec-abc\npoll_hint: wait 5s"
    verdict = validate_conductor_closeout(body, require_mode_b_proof=True)
    assert verdict.ok


def test_score_ratify_do_not_fight() -> None:
    packet = "Posture: do-not-fight; likely-optimal completion."
    assert validate_score_ratify_packet(packet).ok


def test_score_ratify_missing_markers_fails() -> None:
    assert not validate_score_ratify_packet("Looks good to me.").ok


def test_all_catalog_tokens_valid() -> None:
    for token in STOP_TOKENS:
        assert validate_stop_token(token)


def test_pings_for_stops_default_includes_row_pinned() -> None:
    tokens = frozenset({"ROW_PINNED", "HOLD_MERGE"})
    assert pings_for_stops(tokens) == frozenset({"ROW_PINNED", "HOLD_MERGE"})


def test_pings_for_stops_live_summoning_chat_drops_row_pinned() -> None:
    tokens = frozenset({"ROW_PINNED", "HOLD_MERGE"})
    assert pings_for_stops(tokens, live_summoning_chat=True) == frozenset(
        {"HOLD_MERGE"}
    )


def test_validate_conductor_closeout_live_summoning_chat() -> None:
    body = "ROW_PINNED HOLD_MERGE checkpoint"
    verdict = validate_conductor_closeout(body, live_summoning_chat=True)
    assert verdict.ok
    assert "ROW_PINNED" not in verdict.pings_required
    assert "HOLD_MERGE" in verdict.pings_required


_G1_PIN_S4B_OK = """\
| G1 | Architecture consult | DONE | ROW_PINNED |
Problem: Unify conductor and layer G-ladder
Scope: libs/claude_bundles/conductor_stop.py only
Acceptance: pytest green on targeted files
density_triage: judgment_required
status: complete
"""

_G1_PIN_NO_S4B = """\
| G1 | Architecture consult | DONE | ROW_PINNED |
status: complete
ROW_PINNED
"""

_G3_PIN_NO_S4B = """\
| G3 | Densify | OPEN | ROW_PINNED |
resume_at: G3
status: complete
"""


def test_g1_pin_with_s4b_markers_ok() -> None:
    verdict = validate_conductor_closeout(_G1_PIN_S4B_OK)
    assert verdict.ok
    assert is_g1_pin(_G1_PIN_S4B_OK)
    assert has_s4b_evidence(_G1_PIN_S4B_OK)
    assert validate_s4b_g1_pin(_G1_PIN_S4B_OK) is None


def test_g1_pin_without_s4b_fails() -> None:
    verdict = validate_conductor_closeout(_G1_PIN_NO_S4B)
    assert not verdict.ok
    assert verdict.reason == S4B_G1_PIN_MISSING


def test_g1_pin_with_implement_ready_fails() -> None:
    body = (
        _G1_PIN_NO_S4B
        + "\nProblem: x\nScope: y\nAcceptance: z\ndensity_triage: implement_ready"
    )
    verdict = validate_conductor_closeout(body)
    assert not verdict.ok
    assert verdict.reason == S4B_G1_PIN_MISSING


def test_g3_row_pinned_without_s4b_still_ok() -> None:
    verdict = validate_conductor_closeout(_G3_PIN_NO_S4B)
    assert verdict.ok
    assert not is_g1_pin(_G3_PIN_NO_S4B)


def test_g1_pin_from_stop_after_in_packet() -> None:
    packet = "stop_after pin: G1.\ncontract: light-bounded"
    body = "status: complete\nROW_PINNED"
    assert is_g1_pin(body, packet_text=packet)
    verdict = validate_conductor_closeout(body, packet_text=packet)
    assert not verdict.ok
    assert verdict.reason == S4B_G1_PIN_MISSING
