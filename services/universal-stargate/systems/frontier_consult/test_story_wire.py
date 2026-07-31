"""Unit tests for story_id election and association envelope (spec Bind 2)."""

from __future__ import annotations

from systems.frontier_consult.events import FrontierSdkWorkerDispatched
from systems.frontier_consult.story_wire import (
    ASKED_BY_UNRESOLVED,
    PURPOSE_UNSTATED,
    build_association_envelope,
    elect_story_id,
    extract_purpose,
    resolve_asked_by,
)


def test_extract_purpose_from_intent_line() -> None:
    body = "TYPE: DIRECTIVE\nintent: Put the story on the spine.\n## Scope\nfoo\n"
    assert extract_purpose(body) == "Put the story on the spine."


def test_extract_purpose_degrades_to_unstated() -> None:
    assert extract_purpose("TYPE: DIRECTIVE\n## Scope\nfoo\n") == PURPOSE_UNSTATED
    assert extract_purpose("") == PURPOSE_UNSTATED


def test_resolve_asked_by_prefers_from_agent() -> None:
    assert resolve_asked_by(from_agent="cursor", caller_agent="dispatch") == "cursor"


def test_resolve_asked_by_falls_back_to_caller_agent() -> None:
    assert resolve_asked_by(caller_agent="charter-runner") == "charter-runner"


def test_resolve_asked_by_records_unresolved_explicitly() -> None:
    assert resolve_asked_by() == ASKED_BY_UNRESOLVED


def test_elect_story_id_normal_dispatch_request_id() -> None:
    assert (
        elect_story_id(
            request_id="abc123",
            dispatch_id="abc123-deadbeef",
        )
        == "abc123"
    )


def test_elect_story_id_charter_from_packet_path() -> None:
    assert (
        elect_story_id(
            packet_path="tmp/charter-runner/5852-w4.md",
            dispatch_id="req1-abc12345",
        )
        == "5852#4"
    )


def test_elect_story_id_cli_consult_call_id() -> None:
    assert elect_story_id(call_id="call-uuid-1", dispatch_id="auto-abc") == "call-uuid-1"


def test_elect_story_id_nested_auto_dispatch_id() -> None:
    assert elect_story_id(dispatch_id="auto-abc123def456") == "auto-abc123def456"


def test_dispatched_event_association_fields_optional() -> None:
    event = FrontierSdkWorkerDispatched(
        request_id="req1",
        thread_id="5867",
        execution_id="exec-1",
        dispatch_id="req1-abc12345",
    )
    assert "asked_by" not in event.payload
    assert "purpose" not in event.payload
    assert "story_id" not in event.payload

    stamped = FrontierSdkWorkerDispatched(
        request_id="req1",
        thread_id="5867",
        execution_id="exec-1",
        dispatch_id="req1-abc12345",
        asked_by="cursor",
        purpose="ship it",
        story_id="req1",
    )
    assert stamped.payload["asked_by"] == "cursor"
    assert stamped.payload["purpose"] == "ship it"
    assert stamped.payload["story_id"] == "req1"


def test_build_association_envelope_stable_for_unit_of_work() -> None:
    body = "TYPE: DIRECTIVE\nintent: wire the spine\n"
    first = build_association_envelope(
        purpose_body=body,
        caller_agent="cursor",
        request_id="req-stable",
        dispatch_id="req-stable-11111111",
        packet_path="tmp/charter-runner/5852-w9.md",
    )
    second = build_association_envelope(
        purpose_body=body,
        caller_agent="cursor",
        request_id="req-stable",
        dispatch_id="req-stable-11111111",
        packet_path="tmp/charter-runner/5852-w9.md",
    )
    assert first == second
    assert first.story_id == "5852#9"
