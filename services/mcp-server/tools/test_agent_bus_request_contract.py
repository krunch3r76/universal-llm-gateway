"""Intake contract-vocabulary tests for agent_bus.request (Fable §1 / §6 Phase 1)."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from tools.agent_bus.contract_vocab import (
    CANONICAL_CONTRACTS,
    normalize_wire_contract,
)
from tools.agent_bus.request import _request_dispatch


@pytest.mark.parametrize("contract", CANONICAL_CONTRACTS)
def test_canonical_contracts_pass_through(contract: str) -> None:
    intake = normalize_wire_contract(contract)
    assert intake.error is None
    assert intake.contract == contract
    assert intake.deprecated is False


def test_blank_contract_defaults_to_answer() -> None:
    for value in (None, "", "   "):
        intake = normalize_wire_contract(value)
        assert intake.error is None
        assert intake.contract == "answer"


def test_consult_aliases_to_confer_with_deprecation() -> None:
    intake = normalize_wire_contract("Consult")
    assert intake.error is None
    assert intake.contract == "confer"
    assert intake.alias_of == "consult"
    assert intake.deprecation_note is not None
    assert "confer" in intake.deprecation_note


def test_execute_contract_passes_through() -> None:
    intake = normalize_wire_contract("execute")
    assert intake.error is None
    assert intake.contract == "execute"


def test_unknown_contract_fails_loud() -> None:
    intake = normalize_wire_contract("tool-op")
    assert intake.error is not None
    assert intake.error["reason"] == "request_contract_unknown"
    assert intake.error["status_code"] == 422
    assert intake.error["valid_contracts"] == list(CANONICAL_CONTRACTS)
    assert "execute" in intake.error["valid_contracts"]
    assert "execute" in intake.error["error"]


def test_dispatch_rejects_unknown_contract_before_turn_write() -> None:
    """No success-shaped bus turn for a bad contract — reject before enqueue."""
    with patch("tools.agent_bus.request._request_impl") as impl:
        result = _request_dispatch(
            new_slug="bad-contract",
            to="cursor",
            subject="probe",
            body="TYPE: DIRECTIVE\nscope: libs/foo\nvision: mechanical",
            from_agent="web-anthropic",
            contract="tool-op",
        )
    impl.assert_not_called()
    assert result["reason"] == "request_contract_unknown"
    assert result["provided"] == "tool-op"


def test_dispatch_honors_consult_as_confer_and_stamps_note() -> None:
    captured: dict[str, object] = {}
    events: list[tuple[str, dict[str, object]]] = []

    def fake_impl(**kwargs):
        captured.update(kwargs)
        return {"handler_status": "auto-admit-armed"}

    def fake_record(signal: str, **payload: object) -> None:
        events.append((signal, dict(payload)))

    with (
        patch("tools.agent_bus.request._request_impl", side_effect=fake_impl),
        patch("tools.agent_bus.request_intake.record", side_effect=fake_record),
    ):
        result = _request_dispatch(
            thread="6325",
            to="cursor",
            subject="advisory ask",
            body="Should we harden v0 in place?",
            from_agent="web-anthropic",
            contract="consult",
        )
    assert captured["contract"] == "confer"
    assert result["_deprecated"] == {
        "param": "contract",
        "value": "consult",
        "replacement": "confer",
    }
    assert "deprecated" in str(result["notes"])
    assert any(
        sig == "mcp.agentbus.request.contract_deprecated"
        and payload.get("caller") == "web-anthropic"
        and payload.get("contract") == "consult"
        and payload.get("replacement") == "confer"
        for sig, payload in events
    )
