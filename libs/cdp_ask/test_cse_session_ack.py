"""ACK classifier grammar tests."""

from __future__ import annotations

from cdp_ask.cse_session_ack import classify_ack


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
