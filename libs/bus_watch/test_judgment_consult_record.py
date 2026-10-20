"""judgment_consult_coverage — binding turns, consult_ref, packet_shape."""

from __future__ import annotations

import pytest

from bus_watch.judgment_consult_coverage import (
    coverage_ok,
    is_binding_turn,
    packet_lean_ok,
    parse_consult_record,
)

pytestmark = pytest.mark.offline

_SHA = "a" * 64
_LEAN_PACKET = f"""Job: land the harness bind
Evidence:
- cortex://notes/runbooks/coding-boot.md read_sha256={_SHA}
Constraints:
- forward_only
"""

_CONSULT_BLOCK = f"""consult_ref: cursor/gemini-4-argon agent-bus:15851#99 sha256:{_SHA}
check_ref: cdp/opus-5.5 agent-bus:15851#100
"""


def test_is_binding_turn_rejects_partial_payload() -> None:
    assert is_binding_turn({}, root_id="18374") is False
    assert (
        is_binding_turn(
            {"thread": "18374", "from": "cursor", "subject": "PLAN harness"},
            root_id="18374",
        )
        is True
    )
    assert (
        is_binding_turn(
            {"thread": "18374", "from": None, "subject": "PLAN harness"},
            root_id="18374",
        )
        is False
    )
    assert (
        is_binding_turn(
            {"thread": "18374", "from": "cursor", "subject": "CHECKPOINT"},
            root_id="18374",
        )
        is False
    )


def test_parse_consult_record_crlf_and_duplicate_lines() -> None:
    body = (
        "consult_ref: cursor/gemini-4-argon agent-bus:1#1\r\n"
        "consult_ref: cursor/gemini-4-argon agent-bus:1#2\r\n"
        "check_ref: cdp/opus-5.5 agent-bus:1#3\r\n"
    )
    rec = parse_consult_record(body)
    assert rec is not None
    assert rec["consult_ref"] == "agent-bus:1#1"
    assert "consult_sha" not in rec


def test_coverage_ok_rejects_author_or_checker_equal_advisor() -> None:
    body = _LEAN_PACKET + _CONSULT_BLOCK.replace(
        "cursor/gemini-4-argon", "cursor"
    )
    turn = {
        "thread": "18374",
        "from": "cursor",
        "subject": "PLAN foo",
        "body": body,
    }
    ok, reason = coverage_ok(turn, root_id="18374")
    assert ok is False
    assert reason == "advisor_equals_author"

    body2 = _LEAN_PACKET + (
        f"consult_ref: cursor/gemini-4-argon agent-bus:1#1 sha256:{_SHA}\n"
        "check_ref: cursor/gemini-4-argon agent-bus:1#2\n"
    )
    turn2 = {
        "thread": "18374",
        "from": "grok-bot-rag1",
        "subject": "VERDICT bar",
        "body": body2,
    }
    ok2, reason2 = coverage_ok(turn2, root_id="18374")
    assert ok2 is False
    assert reason2 == "advisor_equals_checker"


def test_coverage_ok_requires_opus_5_5_check() -> None:
    body = _LEAN_PACKET + (
        f"consult_ref: cursor/gemini-4-argon agent-bus:1#1 sha256:{_SHA}\n"
        "check_ref: cdp/opus-5 agent-bus:1#2\n"
    )
    turn = {
        "thread": "18374",
        "from": "stage-manager",
        "subject": "RULING baz",
        "body": body,
    }
    ok, reason = coverage_ok(turn, root_id="18374")
    assert ok is False
    assert reason == "check_model_not_opus_5_5"


def test_degraded_exception_does_not_waive_check() -> None:
    body = _LEAN_PACKET + (
        "exception: divergent_ideation_degraded_18362\n"
        f"consult_ref: cursor/gemini-4-argon agent-bus:1#1 sha256:{_SHA}\n"
    )
    turn = {
        "thread": "18374",
        "from": "grok-bot-gc1",
        "subject": "DECISION qux",
        "body": body,
    }
    ok, reason = coverage_ok(turn, root_id="18374")
    assert ok is False
    assert reason == "missing_check_ref"


def test_packet_lean_ok_rejects_oversize_evidence_and_transcript_paste() -> None:
    evidence = "\n".join(
        f"- cortex://notes/x{i}.md read_sha256={_SHA}" for i in range(11)
    )
    big = f"Job: x\nEvidence:\n{evidence}\nConstraints:\n- y\n"
    ok, reason = packet_lean_ok(big)
    assert ok is False
    assert reason == "evidence_over_cap"

    bad_sha = "Job: x\nEvidence:\n- cortex://notes/x.md read_sha256=deadbeef\nConstraints:\n"
    ok2, reason2 = packet_lean_ok(bad_sha)
    assert ok2 is False
    assert reason2 == "evidence_missing_sha"

    order = f"Job: x\nConstraints:\n- y\nEvidence:\n- cortex://notes/x.md read_sha256={_SHA}\n"
    ok3, reason3 = packet_lean_ok(order)
    assert ok3 is False
    assert reason3 == "constraints_not_last"

    transcript = f"Job: x\nEvidence:\n- cortex://notes/x.md read_sha256={_SHA}\nConstraints:\nuser: hello\n"
    ok4, reason4 = packet_lean_ok(transcript)
    assert ok4 is False
    assert reason4 == "transcript_paste"


def test_coverage_ok_accepts_valid_binding() -> None:
    body = _LEAN_PACKET + _CONSULT_BLOCK
    turn = {
        "thread": "18374",
        "from": "grok-bot-gc1",
        "subject": "PLAN ok",
        "body": body,
    }
    ok, reason = coverage_ok(turn, root_id="18374")
    assert ok is True
    assert reason == "ok"
