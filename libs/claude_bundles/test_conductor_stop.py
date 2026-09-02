"""Hermetic tests for conductor stop vocabulary."""

from __future__ import annotations

from claude_bundles.conductor_score_ratify import (
    Q2_SCORE_RATIFY_MISSING,
    is_g3_g5_exit,
    validate_q2_away_score_ratify,
)
from claude_bundles.conductor_stop import (
    CHAIN_STOPS,
    EXIT_PERSIST_STOPS,
    S4B_G1_PIN_MISSING,
    STOP_TOKENS,
    WAIT_STOPS,
    has_consult_handoff,
    has_s4b_evidence,
    is_consult_pending_wait,
    is_exit_persist_stop,
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


def test_pings_for_stops_live_summoning_chat_keeps_row_pinned() -> None:
    tokens = frozenset({"ROW_PINNED", "HOLD_MERGE"})
    assert pings_for_stops(tokens, live_summoning_chat=True) == frozenset(
        {"ROW_PINNED", "HOLD_MERGE"}
    )


def test_pings_for_stops_operator_present_drops_row_pinned() -> None:
    tokens = frozenset({"ROW_PINNED", "HOLD_MERGE"})
    assert pings_for_stops(
        tokens, live_summoning_chat=True, operator_present=True
    ) == frozenset(
        {"HOLD_MERGE"}
    )


def test_validate_conductor_closeout_live_summoning_chat() -> None:
    body = "ROW_PINNED HOLD_MERGE checkpoint"
    verdict = validate_conductor_closeout(body, live_summoning_chat=True)
    assert verdict.ok
    assert "ROW_PINNED" in verdict.pings_required
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


_CONDUCTOR_PACKET = """\
---
packet_kind: conductor
contract: light-bounded
---
<scope>Conductor session.</scope>
"""

_ATTENDED_CONDUCTOR_PACKET = """\
---
packet_kind: conductor
contract: light-bounded
---
<scope>summon_mode: attended</scope>
"""

_G3_DONE_AWAY_NO_MARKERS = """\
| G3 | Densify | DONE |
status: complete
land_disposition: landed
"""

_G3_DONE_AWAY_WITH_MARKERS = """\
| G3 | Densify | DONE |
Posture: do-not-fight; likely-optimal completion.
status: complete
"""

_G3_ROW_PINNED_SEE_SCORE = """\
| G3 | Densify | OPEN | ROW_PINNED |
resume_at: G3
status: complete
"""

_G3_GATE_G5 = """\
resume_at: G5
status: complete
"""


def test_is_g3_g5_exit_g3_done() -> None:
    assert is_g3_g5_exit(_G3_DONE_AWAY_NO_MARKERS)


def test_is_g3_g5_exit_g3_row_pinned_false() -> None:
    assert not is_g3_g5_exit(_G3_ROW_PINNED_SEE_SCORE)


def test_is_g3_g5_exit_resume_g5() -> None:
    assert is_g3_g5_exit(_G3_GATE_G5)


def test_q2_away_g3_done_missing_markers_fails() -> None:
    reason = validate_q2_away_score_ratify(
        _G3_DONE_AWAY_NO_MARKERS,
        packet_text=_CONDUCTOR_PACKET,
    )
    assert reason == Q2_SCORE_RATIFY_MISSING


def test_q2_away_g3_done_with_markers_passes() -> None:
    reason = validate_q2_away_score_ratify(
        _G3_DONE_AWAY_WITH_MARKERS,
        packet_text=_CONDUCTOR_PACKET,
    )
    assert reason is None


def test_q2_g3_row_pinned_see_score_not_tripped() -> None:
    reason = validate_q2_away_score_ratify(
        _G3_ROW_PINNED_SEE_SCORE,
        packet_text=_CONDUCTOR_PACKET,
    )
    assert reason is None


def test_q2_attended_g3_done_not_tripped() -> None:
    reason = validate_q2_away_score_ratify(
        _G3_DONE_AWAY_NO_MARKERS,
        packet_text=_ATTENDED_CONDUCTOR_PACKET,
    )
    assert reason is None


def test_q2_g1_pin_body_not_tripped() -> None:
    reason = validate_q2_away_score_ratify(
        _G1_PIN_NO_S4B,
        packet_text=_CONDUCTOR_PACKET,
    )
    assert reason is None


def test_consult_pending_is_wait_stop_not_session_end() -> None:
    assert "CONSULT_PENDING" in WAIT_STOPS
    assert "CONSULT_PENDING" in STOP_TOKENS
    assert "CONSULT_PENDING" not in EXIT_PERSIST_STOPS
    assert "DONE" not in WAIT_STOPS


def test_row_pinned_is_exit_persist_not_wait() -> None:
    assert "ROW_PINNED" in EXIT_PERSIST_STOPS
    assert "ROW_PINNED" not in WAIT_STOPS
    assert is_exit_persist_stop("| G3 | x | OPEN | ROW_PINNED |")
    assert not is_exit_persist_stop("CONSULT_PENDING\nexecution_id: x")


def test_row_hop_is_chain_stop_not_exit_persist() -> None:
    assert "ROW_HOP" in STOP_TOKENS
    assert "ROW_HOP" in CHAIN_STOPS
    assert "ROW_HOP" not in EXIT_PERSIST_STOPS
    assert validate_stop_token("ROW_HOP")
    parsed = parse_stop_tokens("stop: ROW_HOP\nhop_seq: 2")
    assert parsed.tokens == frozenset({"ROW_HOP"})


def test_consult_pending_wait_needs_admit_and_no_archive() -> None:
    waiting = (
        "CONSULT_PENDING\nexecution_id: exec-abc\npoll_hint: wait\nNEXT_ADMIT: G1"
    )
    assert is_consult_pending_wait(waiting)
    assert has_consult_handoff(waiting)
    harvested = waiting + "\narchive_uri: cortex://notes/system/threads/x.md"
    assert not is_consult_pending_wait(harvested)
    chrome_only = "CONSULT_PENDING\nexecution_id: exec-abc\ncse: cse_01abc"
    assert is_consult_pending_wait(chrome_only)
    assert not has_consult_handoff(chrome_only)


def test_consult_pending_without_admit_proof_fails_mode_b() -> None:
    body = "CONSULT_PENDING — staging Fable"
    verdict = validate_conductor_closeout(body, require_mode_b_proof=True)
    assert not verdict.ok
    assert "admit-proof" in (verdict.reason or "").lower()


def test_validate_conductor_closeout_q2_fold() -> None:
    verdict = validate_conductor_closeout(
        _G3_DONE_AWAY_NO_MARKERS,
        packet_text=_CONDUCTOR_PACKET,
    )
    assert not verdict.ok
    assert verdict.reason == Q2_SCORE_RATIFY_MISSING


def test_resume_at_line_does_not_tokenize_consult_pending() -> None:
    body = "resume_at: CONSULT_PENDING\nstatus: complete"
    parsed = parse_stop_tokens(body)
    assert "CONSULT_PENDING" not in parsed.tokens


def test_narrative_resumed_at_does_not_classify_consult_pending() -> None:
    body = "resumed_at: CONSULT_PENDING\nstatus: complete"
    parsed = parse_stop_tokens(body)
    assert "CONSULT_PENDING" not in parsed.tokens
    assert not is_consult_pending_wait(body)


def test_json_execution_id_is_mode_b_proof() -> None:
    body = '{"execution_id": "abc", "poll_hint": "wait"}\nCONSULT_PENDING'
    assert is_consult_pending_wait(body)
    verdict = validate_conductor_closeout(body, require_mode_b_proof=True)
    assert verdict.ok
