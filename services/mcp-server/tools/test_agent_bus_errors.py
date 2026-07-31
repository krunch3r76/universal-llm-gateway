"""Structured relay error preservation for agent_bus MCP (friction 13695 P0-B)."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools import agent_bus as agent_bus_module  # noqa: E402
from tools._agent_bus_post_guard import reconcile_send_arguments  # noqa: E402


def test_structured_relay_error_surfaces_unread_turns_exist() -> None:
    relay_result = {
        "error": "HTTP 409",
        "status_code": 409,
        "detail": {
            "error": "unread_turns_exist",
            "message": "Read all turns addressed to you before posting",
            "unread_turns": [{"thread": "1414", "turn_number": 7}],
        },
    }
    envelope = agent_bus_module._structured_relay_error(relay_result, op="reply")
    assert envelope is not None
    assert envelope["status_code"] == 409
    assert envelope["reason"] == "unread_turns_exist"
    assert envelope["detail"]["unread_turns"][0]["turn_number"] == 7


def test_reply_impl_preserves_409_detail() -> None:
    relay_result = {
        "error": "HTTP 409",
        "status_code": 409,
        "detail": {
            "error": "unread_turns_exist",
            "message": "Read all turns addressed to you before posting",
            "unread_turns": [{"thread": "1414", "turn_number": 7}],
        },
    }

    with patch.object(agent_bus_module, "_relay", return_value=relay_result):
        with patch.object(agent_bus_module, "record", lambda *_args, **_kwargs: None):
            result = agent_bus_module._reply_impl(
                thread="1414",
                to="claude-web",
                subject="blocked",
                body="attempt",
                after_turn=6,
                from_agent="claude-web",
                status="open",
                mark_read=False,
                close=False,
            )

    assert result["status_code"] == 409
    assert result["reason"] == "unread_turns_exist"
    assert result["detail"]["unread_turns"]


def test_unread_409_carries_mark_read_remediation() -> None:
    relay_result = {
        "error": "HTTP 409",
        "status_code": 409,
        "detail": {
            "error": "unread_turns_exist",
            "message": "Read all turns addressed to you before posting",
            "unread_turns": [
                {"thread": "1485", "turn_number": 5},
                {"thread": "1485", "turn_number": 6},
            ],
            "latest_turn_number": 6,
            "provided_after_turn": 4,
        },
    }
    envelope = agent_bus_module._structured_relay_error(relay_result, op="reply")
    assert envelope is not None
    assert "remediation" in envelope
    assert "through_turn=6" in envelope["remediation"]
    assert "agent=<you>" in envelope["remediation"]
    assert "mark_read" in envelope["error"]


def test_unread_409_remediation_without_turn_list() -> None:
    relay_result = {
        "error": "HTTP 409",
        "status_code": 409,
        "detail": {
            "error": "unread_turns_exist",
            "message": "Read all turns addressed to you before posting",
            "unread_turns": [],
        },
    }
    envelope = agent_bus_module._structured_relay_error(relay_result, op="post")
    assert envelope is not None
    assert "fetch_unread" in envelope["remediation"]


def test_unknown_arg_suggests_canonical_alias() -> None:
    accepted = {"thread", "to", "subject", "body", "after_turn", "from_agent"}
    err = agent_bus_module._unknown_arg_error(
        tool="reply",
        unknown=["thread_id", "agent"],
        accepted=accepted,
    )
    msg = err["error"]
    assert "unsupported argument(s): agent, thread_id" in msg
    assert "'thread_id' → 'thread'" in msg
    assert "'agent' → 'from_agent'" in msg


def test_unknown_arg_no_hint_when_alias_unmapped() -> None:
    err = agent_bus_module._unknown_arg_error(
        tool="reply",
        unknown=["frobnicate"],
        accepted={"thread", "to", "body"},
    )
    assert "Did you mean" not in err["error"]


def test_post_impl_preserves_409_after_route_guard_miss() -> None:
    relay_result: dict[str, Any] = {
        "error": "HTTP 409",
        "status_code": 409,
        "detail": {
            "error": "unread_turns_exist",
            "message": "Read all turns addressed to you before posting",
            "unread_turns": [],
        },
    }

    with patch.object(agent_bus_module, "_relay", return_value=relay_result):
        with patch.object(agent_bus_module, "record", lambda *_args, **_kwargs: None):
            result = agent_bus_module._post_impl(
                slug="blocked-post",
                to="cursor",
                subject="blocked",
                body="attempt",
                from_agent="claude-web",
                summary=None,
            )

    assert result["status_code"] == 409
    assert result["reason"] == "unread_turns_exist"


def test_structured_relay_error_surfaces_turn_already_acknowledged() -> None:
    relay_result = {
        "error": "HTTP 409",
        "status_code": 409,
        "detail": {
            "error": "turn_already_acknowledged",
            "message": "Turn already acknowledged - cannot modify",
            "read_at": "2026-07-11T17:44:38Z",
            "thread": "4865",
            "turn_number": 2,
        },
    }
    envelope = agent_bus_module._structured_relay_error(relay_result, op="update")
    assert envelope is not None
    assert envelope["status_code"] == 409
    assert envelope["reason"] == "turn_already_acknowledged"
    assert envelope["detail"]["read_at"] == "2026-07-11T17:44:38Z"
    assert "remediation" in envelope
    assert 'send(thread="4865"' in envelope["remediation"]


def test_structured_relay_error_normalizes_string_acknowledged_detail() -> None:
    relay_result = {
        "error": "HTTP 409",
        "status_code": 409,
        "detail": "Turn already acknowledged - cannot modify",
    }
    envelope = agent_bus_module._structured_relay_error(relay_result, op="update")
    assert envelope is not None
    assert envelope["reason"] == "turn_already_acknowledged"
    assert envelope["detail"]["error"] == "turn_already_acknowledged"


def test_update_impl_preserves_409_detail() -> None:
    relay_result = {
        "error": "HTTP 409",
        "status_code": 409,
        "detail": {
            "error": "turn_already_acknowledged",
            "message": "Turn already acknowledged - cannot modify",
            "read_at": "2026-07-11T17:44:38Z",
            "thread": "4865",
            "turn_number": 2,
        },
    }
    resolve_turn = (15966, None)

    with patch.object(agent_bus_module, "_resolve_turn_id", return_value=resolve_turn):
        with patch.object(agent_bus_module, "_relay", return_value=relay_result):
            with patch.object(agent_bus_module, "record", lambda *_args, **_kwargs: None):
                result = agent_bus_module._update_impl(
                    thread="4865",
                    turn_number=2,
                    body="revise",
                    append=None,
                    subject=None,
                )

    assert result["status_code"] == 409
    assert result["reason"] == "turn_already_acknowledged"
    assert "send(thread=\"4865\"" in result["remediation"]


def test_wait_dispatch_emits_completed_on_relay_error() -> None:
    recorded: list[tuple[str, dict[str, Any]]] = []

    def _record(signal: str, **payload: Any) -> None:
        recorded.append((signal, payload))

    with patch.object(
        agent_bus_module, "_relay", return_value={"error": "Request to agent-bus timed out"}
    ):
        with patch.object(agent_bus_module, "record", side_effect=_record):
            result = agent_bus_module._wait_dispatch(
                thread="4889",
                after_turn=1,
                wait_seconds=60,
                completion="first_reply_from",
                from_agent="cursor-sdk",
            )

    assert "error" in result
    signals = [s for s, _ in recorded]
    assert "mcp.agentbus.wait.called" in signals
    assert "mcp.agentbus.wait.completed" in signals
    called = next(p for s, p in recorded if s == "mcp.agentbus.wait.called")
    assert called["thread"] == "4889"
    assert called["completion"] == "first_reply_from"
    assert called["wait_seconds"] == 60.0
    assert called["from_agent"] == "cursor-sdk"
    completed = next(p for s, p in recorded if s == "mcp.agentbus.wait.completed")
    assert completed["thread"] == "4889"
    assert completed["status"] == "relay_error"


@pytest.mark.parametrize(
    "bad_to",
    ["cursor-auto", "cursor_auto", "Cursor-Auto"],
)
def test_send_rejects_cursor_auto_spellings(bad_to: str) -> None:
    _, err = reconcile_send_arguments({"to": bad_to, "subject": "s", "body": "b"})
    assert err is not None
    assert err["reason"] == "send_to_cursor_auto_not_addressable"
    assert "request" in err["error"]
    assert "to='cursor'" in err["error"]


@pytest.mark.parametrize(
    "ok_to",
    [
        "cursor",
        "charter-runner",
        "all",
        "cursor-sdk:dispatch:affc3290-47ee-49b4-b96c-db4d47eefa0a",
    ],
)
def test_send_passes_live_addresses(ok_to: str) -> None:
    _, err = reconcile_send_arguments({"to": ok_to, "subject": "s", "body": "b"})
    assert err is None
