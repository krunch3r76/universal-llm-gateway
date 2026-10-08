"""Intake contract-vocabulary tests for agent_bus.request (Fable §1 / §6 Phase 1)."""

from __future__ import annotations

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


def test_seed_contract_passes_through() -> None:
    intake = normalize_wire_contract("seed")
    assert intake.error is None
    assert intake.contract == "seed"


def test_unknown_contract_fails_loud() -> None:
    intake = normalize_wire_contract("tool-op")
    assert intake.error is not None
    assert intake.error["reason"] == "request_contract_unknown"
    assert intake.error["status_code"] == 422
    assert intake.error["valid_contracts"] == list(CANONICAL_CONTRACTS)
    assert "execute" in intake.error["valid_contracts"]
    assert "execute" in intake.error["error"]


def test_dispatch_refuses_before_turn_write() -> None:
    """Retired entry returns before contract, lane, or turn write."""
    result = _request_dispatch(
        new_slug="bad-contract",
        to="cursor",
        subject="probe",
        body="TYPE: DIRECTIVE\nscope: libs/foo\nvision: mechanical",
        from_agent="web-anthropic",
        contract="tool-op",
    )
    assert result["reason"] == "cursor_auto_retired"


def test_select_lane_explicit_b() -> None:
    """select_lane honors an explicit B."""
    from pathlib import Path

    from services.git_integration_worker.cursor_sdk_lane_select import select_lane
    from services.git_integration_worker.models.cursor_api import CursorDispatchRequest

    req = CursorDispatchRequest(
        thread_id="7224",
        model="composer-2.5",
        dispatch_id="d-lane-rt",
        execution_id="e-lane-rt",
        message="do work",
        lane="B",
    )
    selected, _advisories, reason = select_lane(
        req=req,
        regime_active=False,
        source_repo=Path("/mnt/torus/projects/universal-llm-gateway"),
        files_expected=["services/mcp-server/tools/agent_bus/request.py"],
        contract="implement",
    )
    assert selected == "B"
    assert reason == "explicit"


def test_select_lane_default_opt_out() -> None:
    """select_lane with no lane stays opt_out."""
    from pathlib import Path

    from services.git_integration_worker.cursor_sdk_lane_select import select_lane
    from services.git_integration_worker.models.cursor_api import CursorDispatchRequest

    req = CursorDispatchRequest(
        thread_id="7224",
        model="composer-2.5",
        dispatch_id="d-lane-default",
        execution_id="e-lane-default",
        message="do work",
    )
    selected, _advisories, reason = select_lane(
        req=req,
        regime_active=False,
        source_repo=Path("/mnt/torus/projects/universal-llm-gateway"),
        files_expected=["services/mcp-server/tools/agent_bus/request.py"],
        contract="implement",
    )
    assert selected == "A"
    assert reason == "opt_out"
