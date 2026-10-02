"""Tests for cursor-sdk check/review role delivery bridge."""

from __future__ import annotations

from implement_admission.check_review_substrate import cursor_role_turn_sender

from systems.frontier_consult.cursor_sdk_generate import CURSOR_SDK_REPLY_SEAT
from systems.frontier_consult.cursor_sdk_role_delivery import (
    _conforming_check_closeout,
    build_role_labeled_turn_body,
    closeout_for_job,
)
from systems.frontier_consult.handoff_response import build_handoff_result

_TABLE_CONTRACTS = (
    "",
    "none",
    "freeform",
    "check-review",
    "code-review",
    "delivery-review",
    "implement",
)
_TABLE_MODELS = (
    "cursor/grok-4.7",
    "cursor/gpt-5.6-luna",
    "cursor/gemini-3.6-flash",
)

def test_closeout_grammar_follows_job_not_model() -> None:
    findings = "Findings ok."
    shaped = closeout_for_job("check-review", findings)
    assert "FILE_EVIDENCE_PATHS:" in shaped
    plain = closeout_for_job("freeform", findings)
    assert plain == findings
    assert "FILE_EVIDENCE_PATHS:" not in plain


def test_role_turn_sender_table() -> None:
    """Exactly one contract×model cell returns skeptic; the other twenty return None."""
    hits = []
    for contract in _TABLE_CONTRACTS:
        for model in _TABLE_MODELS:
            sender = cursor_role_turn_sender(contract, model)
            if sender is not None:
                hits.append((contract, model, sender))
    assert hits == [("check-review", "cursor/grok-4.7", "skeptic")]
    assert len(_TABLE_CONTRACTS) * len(_TABLE_MODELS) == 21


def test_poll_hint_stays_cursor_sdk_when_role_bridge_eligible() -> None:
    """Friction 24229: bridge may fail closed; wait identity = SDK closeout author."""
    model = "cursor/grok-4.7"
    assert cursor_role_turn_sender("check-review", model) == "skeptic"
    fields = build_handoff_result(
        thread_id="5094",
        to_agent="cursor-sdk:dispatch:95b09ed1",
        reply_from_agent=CURSOR_SDK_REPLY_SEAT,
    )
    assert fields["reply_from_agent"] == CURSOR_SDK_REPLY_SEAT
    assert fields["poll_hint"]["arguments"]["from_agent"] == CURSOR_SDK_REPLY_SEAT
    assert fields["poll_hint"]["arguments"]["from_agent"] != "skeptic"


def test_conforming_closeout_requires_file_evidence_paths() -> None:
    body = "Findings ok.\n\nFILE_EVIDENCE_PATHS:\n- workspaces://universal-llm-gateway/foo.py"
    parsed = _conforming_check_closeout(body)
    assert parsed is not None
    findings, paths = parsed
    assert "Findings ok." in findings
    assert paths == ["workspaces://universal-llm-gateway/foo.py"]


def test_empty_closeout_fails_closed() -> None:
    assert _conforming_check_closeout("") is None
    assert _conforming_check_closeout("no evidence block") is None


def test_build_role_labeled_turn_body_preserves_block() -> None:
    body = build_role_labeled_turn_body(
        "RATIFY",
        ["cortex://notes/system/specs/foo.md"],
    )
    assert "FILE_EVIDENCE_PATHS:" in body
    assert "cortex://notes/system/specs/foo.md" in body
