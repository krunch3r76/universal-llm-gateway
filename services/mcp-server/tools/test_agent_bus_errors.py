"""Structured relay error preservation for agent_bus MCP (friction 13695 P0-B)."""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from fastmcp import FastMCP

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools import agent_bus as agent_bus_module  # noqa: E402
from tools._agent_bus_post_guard import reconcile_send_arguments  # noqa: E402
from tools.agent_bus import register_agent_bus_tools  # noqa: E402
from tools.agent_bus._arg_rewrite import reconcile_dispatch_arguments  # noqa: E402
from tools._agent_bus_read import register_agent_bus_read_tool  # noqa: E402


@pytest.fixture
def agent_bus_fn(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(agent_bus_module, "record", lambda *a, **k: None)
    mcp = FastMCP("test-agent-bus-arg-rewrite")
    register_agent_bus_tools(mcp)
    tools = asyncio.run(mcp.list_tools())
    return next(t for t in tools if t.name == "agent_bus").fn


@pytest.fixture
def agent_bus_read_fn(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(agent_bus_module, "record", lambda *a, **k: None)
    mcp = FastMCP("test-agent-bus-read-arg-rewrite")
    register_agent_bus_read_tool(mcp)
    tools = asyncio.run(mcp.list_tools())
    return next(t for t in tools if t.name == "agent_bus_read").fn


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


def test_wait_dispatch_forwards_execution_id_query_param() -> None:
    captured: list[str] = []

    def _relay(_service: str, _method: str, path: str) -> dict[str, Any]:
        captured.append(path)
        return {"status": "no_new_turn", "complete": False, "producers": []}

    with patch.object(agent_bus_module, "_relay", side_effect=_relay):
        with patch.object(agent_bus_module, "record", lambda *_args, **_kwargs: None):
            result = agent_bus_module._wait_dispatch(
                thread="5010",
                after_turn=2,
                wait_seconds=0,
                completion="status:done",
                execution_id="exec-5010",
            )

    assert "error" not in result
    assert captured
    assert "execution_id=exec-5010" in captured[0]


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
    assert "team_dispatch" in err["error"]
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


def test_wait_timeout_seconds_rewrites_to_wait_seconds(agent_bus_fn) -> None:
    recorded: list[tuple[str, dict[str, Any]]] = []

    def _record(signal: str, **payload: Any) -> None:
        recorded.append((signal, payload))

    with patch.object(agent_bus_module.lifecycle, "MAX_WAIT_SECONDS", 320.0):
        with patch.object(
            agent_bus_module,
            "_relay",
            return_value={"status": "no_new_turn", "complete": False},
        ):
            with patch.object(agent_bus_module, "record", side_effect=_record):
                result = asyncio.run(
                    agent_bus_fn(
                        tool="wait",
                        arguments=json.dumps(
                            {
                                "thread": "15609",
                                "timeout_seconds": 290,
                                "from_agent": "grok-bot",
                                "completion": "thread_closed",
                            }
                        ),
                    )
                )

    assert "error" not in result
    called = next(p for s, p in recorded if s == "mcp.agentbus.wait.called")
    assert called["wait_seconds"] == 290.0


def test_wait_rejects_timeout_seconds_with_wait_seconds(agent_bus_fn) -> None:
    recorded: list[tuple[str, dict[str, Any]]] = []

    def _record(signal: str, **payload: Any) -> None:
        recorded.append((signal, payload))

    with patch.object(agent_bus_module, "record", side_effect=_record):
        result = asyncio.run(
            agent_bus_fn(
                tool="wait",
                arguments=json.dumps(
                    {
                        "thread": "15609",
                        "wait_seconds": 10,
                        "timeout_seconds": 290,
                        "from_agent": "grok-bot",
                    }
                ),
            )
        )

    assert "error" in result
    assert result.get("reason") == "wait_timeout_conflict"
    assert not any(s == "mcp.agentbus.wait.called" for s, _ in recorded)


def test_mark_read_agent_through_turn_unchanged(agent_bus_fn) -> None:
    captured: list[dict[str, Any]] = []

    def _relay(
        service: str, method: str, path: str, **kwargs: Any
    ) -> dict[str, Any]:
        del service, method, path
        captured.append(kwargs.get("body") or {})
        return {"marked_read": 1}

    with patch.object(agent_bus_module, "_relay", side_effect=_relay):
        result = asyncio.run(
            agent_bus_fn(
                tool="mark_read",
                arguments=json.dumps(
                    {
                        "thread": "15741",
                        "agent": "grok-bot",
                        "through_turn": 12,
                    }
                ),
            )
        )

    assert "error" not in result
    assert captured[0]["agent"] == "grok-bot"
    assert captured[0]["through_turn"] == 12
    assert "turn_numbers" not in captured[0]


def test_mark_read_reader_and_turn_number_rewrite() -> None:
    parsed, err, advisories = reconcile_dispatch_arguments(
        "mark_read",
        {"thread": "15741", "reader": "grok-bot", "turn_number": 25},
    )
    assert err is None
    assert advisories
    assert parsed["agent"] == "grok-bot"
    assert parsed["turn_numbers"] == [25]
    assert "reader" not in parsed
    assert "turn_number" not in parsed


def test_mark_read_reader_turn_number_reaches_handler(agent_bus_fn) -> None:
    captured: list[dict[str, Any]] = []

    def _relay(
        service: str, method: str, path: str, **kwargs: Any
    ) -> dict[str, Any]:
        del service, method, path
        captured.append(kwargs.get("body") or {})
        return {"marked_read": 1}

    with patch.object(agent_bus_module, "_relay", side_effect=_relay):
        result = asyncio.run(
            agent_bus_fn(
                tool="mark_read",
                arguments=json.dumps(
                    {
                        "thread": "15741",
                        "reader": "grok-bot",
                        "turn_number": 25,
                    }
                ),
            )
        )

    assert "error" not in result
    assert captured[0]["turn_numbers"] == [25]


def test_fetch_after_turn_wires_to_turns(agent_bus_fn) -> None:
    captured: list[str] = []

    def _relay(_service: str, _method: str, path: str, **_kwargs: Any) -> list[Any]:
        captured.append(path)
        return []

    with patch.object(agent_bus_module, "_relay", side_effect=_relay):
        result = asyncio.run(
            agent_bus_fn(
                tool="fetch",
                arguments=json.dumps({"thread": "15609", "after_turn": 3}),
            )
        )

    assert "error" not in result
    assert captured
    assert "after_turn=3" in captured[0]
    assert "argument_rewrite_advisory" in result
    assert "after_turn" in result["argument_rewrite_advisory"]


def test_get_turn_alias_rewrite(agent_bus_read_fn) -> None:
    captured: list[str] = []

    def _relay(_service: str, _method: str, path: str, **_kwargs: Any) -> dict[str, Any]:
        captured.append(path)
        return {"id": 1, "thread": "15609", "turn_number": 7, "read_at": "2026-01-01T00:00:00Z"}

    with patch.object(agent_bus_module, "_relay", side_effect=_relay):
        result = agent_bus_read_fn(
            tool="get",
            arguments=json.dumps(
                {"thread": "15609", "turn": 7, "mark_read": True}
            ),
        )

    assert "error" not in result
    assert "turn_number=7" in captured[0]
    assert "argument_rewrite_advisory" in result
    assert "turn" in result["argument_rewrite_advisory"]


def test_get_mark_read_true_patches_read(agent_bus_read_fn) -> None:
    paths: list[str] = []

    def _relay(_service: str, method: str, path: str, **_kwargs: Any) -> dict[str, Any]:
        paths.append(f"{method} {path}")
        if method == "GET":
            return {"id": 99, "thread": "15741", "turn_number": 2, "read_at": None}
        return {"status": "ok", "read_at": "2026-01-02T00:00:00Z"}

    with patch.object(agent_bus_module, "_relay", side_effect=_relay):
        result = agent_bus_read_fn(
            tool="get",
            arguments=json.dumps(
                {"thread": "15741", "turn_number": 2, "mark_read": True}
            ),
        )

    assert "error" not in result
    assert any("PATCH /turns/99/read" in p for p in paths)


def test_threads_limit_maps_to_last(agent_bus_fn) -> None:
    captured: list[str] = []

    def _relay(_service: str, _method: str, path: str, **_kwargs: Any) -> dict[str, Any]:
        captured.append(path)
        return {"threads": []}

    with patch.object(agent_bus_module, "_relay", side_effect=_relay):
        result = asyncio.run(
            agent_bus_fn(
                tool="threads",
                arguments=json.dumps({"status": "active", "limit": 25}),
            )
        )

    assert "error" not in result
    assert "limit=25" in captured[0]
    assert "argument_rewrite_advisory" in result


def test_send_idempotency_key_on_new_slug(agent_bus_fn) -> None:
    captured: list[dict[str, Any]] = []

    def _relay(
        _service: str, _method: str, _path: str, **kwargs: Any
    ) -> dict[str, Any]:
        captured.append(kwargs.get("body") or {})
        return {
            "send_path": "new_thread",
            "thread": {"id": "16001"},
            "turn": {"turn_number": 1},
        }

    wake_ok = type("Wake", (), {"ok": True})()
    with patch.object(agent_bus_module, "_relay", side_effect=_relay):
        with patch(
            "claude_bundles.mission_close_wake.validate_mission_close_wake",
            return_value=wake_ok,
        ):
            with patch(
                "claude_bundles.lane_a_closeout_checkpoint.validate_lane_a_closeout_checkpoint",
                return_value=wake_ok,
            ):
                result = asyncio.run(
                    agent_bus_fn(
                        tool="send",
                        arguments=json.dumps(
                            {
                                "new_slug": "retry-safe-slug",
                                "to": "cursor",
                                "subject": "s",
                                "body": "b",
                                "from_agent": "grok-bot",
                                "idempotency_key": "idem-key-12345678",
                            }
                        ),
                    )
                )

    assert "error" not in result
    assert captured[0].get("idempotency_key") == "idem-key-12345678"


def test_send_idempotency_key_dropped_on_continue(agent_bus_fn) -> None:
    captured: list[dict[str, Any]] = []

    def _relay(
        _service: str, _method: str, _path: str, **kwargs: Any
    ) -> dict[str, Any]:
        captured.append(kwargs.get("body") or {})
        return {
            "send_path": "continue",
            "thread": {"id": "15609"},
            "turn": {"turn_number": 2},
        }

    wake_ok = type("Wake", (), {"ok": True})()
    with patch.object(agent_bus_module, "_relay", side_effect=_relay):
        with patch(
            "claude_bundles.mission_close_wake.validate_mission_close_wake",
            return_value=wake_ok,
        ):
            with patch(
                "claude_bundles.lane_a_closeout_checkpoint.validate_lane_a_closeout_checkpoint",
                return_value=wake_ok,
            ):
                result = asyncio.run(
                    agent_bus_fn(
                        tool="send",
                        arguments=json.dumps(
                            {
                                "thread": "15609",
                                "to": "cursor",
                                "subject": "s",
                                "body": "b",
                                "from_agent": "grok-bot",
                                "idempotency_key": "idem-key-12345678",
                            }
                        ),
                    )
                )

    assert "error" not in result
    assert "idempotency_key" not in captured[0]
    assert "argument_rewrite_advisory" in result
    assert "idempotency_key" in result["argument_rewrite_advisory"]


def test_create_thread_drops_from_with_advisory(agent_bus_fn) -> None:
    captured: list[dict[str, Any]] = []

    def _relay(
        _service: str, _method: str, _path: str, **kwargs: Any
    ) -> dict[str, Any]:
        captured.append(kwargs.get("body") or {})
        return {"thread": {"id": "16002", "slug": "house-x"}}

    with patch.object(agent_bus_module, "_relay", side_effect=_relay):
        result = asyncio.run(
            agent_bus_fn(
                tool="create_thread",
                arguments=json.dumps(
                    {
                        "slug": "house-x",
                        "from_agent": "grok-bot",
                        "from": "grok-bot",
                    }
                ),
            )
        )

    assert "error" not in result
    body = captured[0]
    assert "from" not in body
    assert "from_agent" not in body
    assert "argument_rewrite_advisory" in result


def test_mark_read_turn_number_list_rejected_with_hint(agent_bus_fn) -> None:
    result = asyncio.run(
        agent_bus_fn(
            tool="mark_read",
            arguments=json.dumps(
                {"thread": "15741", "agent": "grok-bot", "turn_number": [1, 2]}
            ),
        )
    )
    assert "error" in result
    assert result.get("reason") == "mark_read_turn_number_invalid"
    assert "turn_numbers" in result["error"]


def test_wait_timeout_s_and_timeout_seconds_numeric_agree(agent_bus_fn) -> None:
    with patch.object(agent_bus_module.lifecycle, "MAX_WAIT_SECONDS", 320.0):
        with patch.object(
            agent_bus_module,
            "_relay",
            return_value={"status": "no_new_turn", "complete": False},
        ):
            result = asyncio.run(
                agent_bus_fn(
                    tool="wait",
                    arguments=json.dumps(
                        {
                            "thread": "15609",
                            "timeout_seconds": 290,
                            "timeout_s": 290.0,
                            "from_agent": "grok-bot",
                        }
                    ),
                )
            )

    assert "error" not in result


@pytest.mark.parametrize("bus_fn_fixture", ["agent_bus_fn", "agent_bus_read_fn"])
def test_wait_mark_read_true_accepted_on_both_surfaces(
    bus_fn_fixture: str, request: pytest.FixtureRequest
) -> None:
    bus_fn = request.getfixturevalue(bus_fn_fixture)
    rejected: list[dict[str, Any]] = []

    def _record(signal: str, **payload: Any) -> None:
        if signal == "mcp.agentbus.dispatch.rejected":
            rejected.append(payload)

    with patch.object(agent_bus_module, "_relay", return_value={"complete": False}):
        with patch.object(agent_bus_module, "record", side_effect=_record):
            invoke = bus_fn(
                tool="wait",
                arguments=json.dumps(
                    {
                        "thread": "15609",
                        "from_agent": "grok-bot",
                        "mark_read": True,
                        "completion": "thread_closed",
                    }
                ),
            )
            result = asyncio.run(invoke) if asyncio.iscoroutine(invoke) else invoke

    assert "error" not in result
    assert not rejected


def test_wait_mark_read_true_emits_canonical_pattern_advisory(agent_bus_fn) -> None:
    with patch.object(
        agent_bus_module,
        "_relay",
        return_value={"complete": False, "status": "no_new_turn"},
    ):
        result = asyncio.run(
            agent_bus_fn(
                tool="wait",
                arguments=json.dumps(
                    {
                        "thread": "15609",
                        "from_agent": "grok-bot",
                        "mark_read": True,
                        "completion": "thread_closed",
                    }
                ),
            )
        )

    assert "error" not in result
    advisory = result.get("argument_rewrite_advisory", "")
    assert "wait(...)" in advisory
    assert "mark_read(thread, through_turn=" in advisory


def test_wait_mark_read_calls_through_turn_bulk_mark_for_qualifying_turn() -> None:
    calls: list[tuple[str, str, str, dict[str, Any]]] = []

    def _relay(
        service: str, method: str, path: str, **kwargs: Any
    ) -> dict[str, Any]:
        calls.append((service, method, path, kwargs.get("body") or {}))
        if method == "GET" and "/wait" in path:
            return {
                "complete": True,
                "qualifying_reply_turn": 7,
                "status": "complete",
            }
        return {"marked_read": 1}

    with patch.object(agent_bus_module, "_relay", side_effect=_relay):
        with patch.object(agent_bus_module, "record", lambda *_a, **_k: None):
            result = agent_bus_module._wait_dispatch(
                thread="15609",
                from_agent="grok-bot",
                mark_read=True,
                completion="first_reply_from",
            )

    assert "error" not in result
    patch_calls = [
        c for c in calls if c[1] == "PATCH" and c[2].endswith("/turns/read-state")
    ]
    assert len(patch_calls) == 1
    body = patch_calls[0][3]
    assert body == {"through_turn": 7, "agent": "grok-bot"}


def test_wait_mark_read_store_contract_uses_bulk_through_turn_not_turn_list() -> None:
    """Recipient filter lives in agent-bus store; MCP sends through_turn + agent only."""

    def _relay(
        service: str, method: str, path: str, **kwargs: Any
    ) -> dict[str, Any]:
        if method == "GET":
            return {"complete": True, "qualifying_reply_turn": 3}
        body = kwargs.get("body") or {}
        assert "turn_numbers" not in body
        return {"marked_read": 0}

    with patch.object(agent_bus_module, "_relay", side_effect=_relay):
        with patch.object(agent_bus_module, "record", lambda *_a, **_k: None):
            result = agent_bus_module._wait_dispatch(
                thread="15609",
                from_agent="grok-bot",
                mark_read=True,
                completion="first_reply_from",
            )

    assert result.get("complete") is True


def test_wait_incomplete_with_mark_read_skips_mark_relay() -> None:
    calls: list[tuple[str, str]] = []

    def _relay(service: str, method: str, path: str, **_kwargs: Any) -> dict[str, Any]:
        calls.append((method, path))
        return {"complete": False, "status": "no_new_turn", "qualifying_reply_turn": None}

    with patch.object(agent_bus_module, "_relay", side_effect=_relay):
        with patch.object(agent_bus_module, "record", lambda *_a, **_k: None):
            result = agent_bus_module._wait_dispatch(
                thread="15609",
                from_agent="grok-bot",
                mark_read=True,
                completion="first_reply_from",
            )

    assert result.get("complete") is False
    assert not any(m == "PATCH" for m, _ in calls)


def test_unmapped_argument_still_rejected(agent_bus_fn) -> None:
    result = asyncio.run(
        agent_bus_fn(
            tool="fetch",
            arguments=json.dumps({"thread": "15609", "frobnicate": 1}),
        )
    )
    assert "error" in result
    assert "frobnicate" in result["error"]
    assert "unsupported argument" in result["error"]
