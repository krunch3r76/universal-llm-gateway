"""agent_bus request entry refuses before request_id intake (a:38728)."""

from __future__ import annotations

from unittest.mock import patch

from tools.agent_bus.request import _request_dispatch
from tools.agent_bus.request_intake import (
    reset_request_id_registry_for_tests,
    resolve_request_id_intake,
)


def test_request_id_path_refuses_before_send() -> None:
    with patch("tools.agent_bus.request.record") as record_mock:
        result = _request_dispatch(
            new_slug="rid-echo",
            to="cursor",
            subject="probe",
            body="brief",
            from_agent="web-anthropic",
            contract="answer",
            request_id="rid-6328-a",
        )
    assert result == {
        "error": (
            "cursor-auto is retired (a:38728). Code commission: team_dispatch on ulg-code. "
            "Life CSE: life_dispatch. A bus turn: agent_bus send."
        ),
        "reason": "cursor_auto_retired",
    }
    record_mock.assert_called_once_with(
        "mcp.agentbus.request.rejected",
        reason="cursor_auto_retired",
    )


def setup_function() -> None:
    reset_request_id_registry_for_tests()


def test_resolve_request_id_duplicate() -> None:
    first = resolve_request_id_intake(
        "dup-key",
        thread_id="1",
        contract="answer",
        from_agent="web-anthropic",
    )
    assert first.error is None
    second = resolve_request_id_intake(
        "dup-key",
        thread_id="1",
        contract="answer",
        from_agent="web-anthropic",
    )
    assert second.error is not None
    assert second.error["reason"] == "duplicate_request_id"


def test_resolve_request_id_mints_when_absent() -> None:
    minted = resolve_request_id_intake(
        None,
        thread_id="1",
        contract="answer",
        from_agent="web-anthropic",
    )
    assert minted.error is None
    assert minted.request_id


def test_agent_bus_request_refuses_before_unknown_args() -> None:
    import asyncio

    from tools.agent_bus import register_agent_bus_tools

    class _Rec:
        def tool(self, **_kwargs):
            def deco(fn):
                self.fn = fn
                return fn

            return deco

    rec = _Rec()
    register_agent_bus_tools(rec)  # type: ignore[arg-type]
    result = asyncio.run(
        rec.fn(tool="request", arguments='{"bogus":1,"from":"nobody"}')
    )
    assert result["reason"] == "cursor_auto_retired"
