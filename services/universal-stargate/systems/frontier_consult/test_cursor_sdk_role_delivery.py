"""Tests for cursor-sdk check/review role delivery bridge."""

from __future__ import annotations

import pytest

from systems.frontier_consult.cursor_sdk_generate import CURSOR_SDK_REPLY_SEAT
from systems.frontier_consult.cursor_sdk_role_delivery import (
    _conforming_check_closeout,
    build_role_labeled_turn_body,
    resolve_delivery_from_role,
    should_bridge_cursor_check_review,
)
from systems.frontier_consult.handoff_response import build_handoff_result


def test_should_bridge_light_bounded_luna() -> None:
    assert should_bridge_cursor_check_review(
        contract="light-bounded",
        resolved_model="cursor/gpt-5.6-luna",
    )
    assert not should_bridge_cursor_check_review(
        contract="implement",
        resolved_model="cursor/gpt-5.6-luna",
    )


def test_poll_hint_stays_cursor_sdk_when_role_bridge_eligible() -> None:
    """Friction 24229: bridge may fail closed; wait identity = SDK closeout author."""
    model = "cursor/gpt-5.6-luna"
    assert should_bridge_cursor_check_review(
        contract="light-bounded",
        resolved_model=model,
    )
    assert resolve_delivery_from_role(model) == "reviewer"
    # Admit-time poll_hint must still key on the guaranteed closeout seat.
    fields = build_handoff_result(
        thread_id="5094",
        to_agent="cursor-sdk:dispatch:95b09ed1",
        reply_from_agent=CURSOR_SDK_REPLY_SEAT,
    )
    assert fields["reply_from_agent"] == CURSOR_SDK_REPLY_SEAT
    assert fields["poll_hint"]["arguments"]["from_agent"] == CURSOR_SDK_REPLY_SEAT
    assert fields["poll_hint"]["arguments"]["from_agent"] != "reviewer"


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
