"""ACK classifier grammar tests."""

from __future__ import annotations

from cdp_ask.cse_session_ack import classify_ack, marker_type


def test_typed_ack_requires_marker_or_birth_id() -> None:
    body = "TYPE: SEAT_STAND_DOWN_ACK\nmarker: 7246-stand-down-successor-20260815"
    assert (
        classify_ack(body, marker="7246-stand-down-successor-20260815") == "typed_ack"
    )
    assert classify_ack(body) == "ordinary_content"


def test_ordinary_prose_never_typed_ack() -> None:
    assert classify_ack("Thanks, standing down now.") == "ordinary_content"


def test_no_proof_on_empty() -> None:
    assert classify_ack("") == "no_proof"


def test_marker_type_stand_down_ack() -> None:
    assert marker_type("TYPE: SEAT_STAND_DOWN_ACK\nmarker: x") == "stand_down_ack"
    assert marker_type("type: seat_stand_down_ack") == "stand_down_ack"


def test_marker_type_successor_attestation() -> None:
    assert marker_type("TYPE: SUCCESSOR_ATTESTATION\n") == "successor_attestation"


def test_marker_type_empty_and_ordinary() -> None:
    assert marker_type("") is None
    assert marker_type("Thanks, standing down now.") is None


def test_marker_type_bare_non_ack() -> None:
    assert marker_type("TYPE: SEAT_STAND_DOWN\n") is None


def test_marker_type_both_successor_later() -> None:
    body = "TYPE: SEAT_STAND_DOWN_ACK\nTYPE: SUCCESSOR_ATTESTATION\n"
    assert marker_type(body) == "successor_attestation"


def test_marker_type_both_stand_down_later() -> None:
    body = "TYPE: SUCCESSOR_ATTESTATION\nTYPE: SEAT_STAND_DOWN_ACK\n"
    assert marker_type(body) == "stand_down_ack"
