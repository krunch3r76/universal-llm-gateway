"""request_id intake for agent_bus.request (Fable §5)."""

from __future__ import annotations

from unittest.mock import patch

from tools.agent_bus.request import _request_dispatch
from tools.agent_bus.request_intake import reset_request_id_registry_for_tests


def setup_function() -> None:
    reset_request_id_registry_for_tests()


def test_request_id_echoed_when_caller_supplies() -> None:
    with (
        patch("tools.agent_bus.request._request_impl") as impl,
        patch("tools.agent_bus.request.resolve_contract_intake") as contract_intake,
    ):
        contract_intake.return_value.error = None
        contract_intake.return_value.contract = "execute"
        contract_intake.return_value.deprecated = False
        impl.side_effect = lambda **kwargs: {
            "handler_status": "auto-admit-armed",
            "request_id": kwargs.get("request_id"),
        }
        result = _request_dispatch(
            new_slug="rid-echo",
            to="cursor",
            subject="probe",
            body="TYPE: DIRECTIVE\ncontract: execute\ntool_op: email.pull\neffects_expected: x\n",
            from_agent="web-anthropic",
            contract="execute",
            request_id="rid-6328-a",
        )
    assert impl.call_args.kwargs["request_id"] == "rid-6328-a"
    assert result["request_id"] == "rid-6328-a"


def test_duplicate_request_id_refused_before_impl() -> None:
    with patch("tools.agent_bus.request._request_impl") as impl:
        _request_dispatch(
            new_slug="rid-dup-1",
            to="cursor",
            subject="first",
            body="brief",
            from_agent="web-anthropic",
            contract="answer",
            request_id="dup-key-6328",
        )
        result = _request_dispatch(
            thread="6328",
            to="cursor",
            subject="second",
            body="brief",
            from_agent="web-anthropic",
            contract="answer",
            request_id="dup-key-6328",
        )
    assert result["reason"] == "duplicate_request_id"
    assert impl.call_count == 1


def test_absent_request_id_minted_and_echoed() -> None:
    captured: dict[str, object] = {}

    def fake_impl(**kwargs):
        captured.update(kwargs)
        return {
            "handler_status": "no-auto-handler",
            "request_id": kwargs.get("request_id"),
        }

    with patch("tools.agent_bus.request._request_impl", side_effect=fake_impl):
        result = _request_dispatch(
            new_slug="rid-mint",
            to="cursor",
            subject="probe",
            body="brief",
            from_agent="web-anthropic",
            contract="answer",
        )
    assert captured.get("request_id")
    assert result.get("request_id") == captured.get("request_id")
