"""Regression: enrolled-root cursor-auto admits reach GIW prompt-expand prelude."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from systems.frontier_consult.prompt_expand_prelude import ExpandRun

from services.git_integration_worker.app import create_app
from services.git_integration_worker.cursor_auto.nested_sdk import (
    resolve_enrolled_root_fields,
)
from services.git_integration_worker.cursor_dispatch_ledger import CursorDispatchLedger
from services.git_integration_worker.cursor_sdk_prompt_expand import (
    expand_contract_for_admit,
    giw_should_expand_prompt,
)
from services.git_integration_worker.models.cursor_api import CursorDispatchRequest


@pytest.mark.offline
def test_resolve_enrolled_root_fields_direct_root() -> None:
    fields = resolve_enrolled_root_fields("10479")
    assert fields == {
        "continuity_root_thread_id": "10479",
        "parent_dispatch_thread_id": "10479",
    }


@pytest.mark.offline
def test_expand_contract_preserves_operator_implement() -> None:
    req = CursorDispatchRequest(
        thread_id="11767",
        model="cursor/composer-2.5",
        dispatch_id="auto-test",
        execution_id="exec-auto-test",
        message="# Dispatch\n\nFix the wiring.",
        handoff_contract="pure-mechanical",
        operator_contract="implement",
        continuity_root_thread_id="10479",
        parent_dispatch_thread_id="10479",
        admitted_via="cursor-auto",
    )
    assert expand_contract_for_admit(req, handoff_contract="pure-mechanical") == "implement"
    assert (
        giw_should_expand_prompt(
            req,
            req.message or "",
            handoff_contract="pure-mechanical",
        )
        is False
    )


@pytest.mark.offline
def test_giw_should_not_expand_wake_or_mechanical() -> None:
    wake_req = CursorDispatchRequest(
        thread_id="10479",
        model="cursor/composer-2.5",
        dispatch_id="auto-wake",
        execution_id="exec-wake",
        message="WAKE — liaison headless successor",
        handoff_contract="none",
        continuity_root_thread_id="10479",
    )
    assert (
        giw_should_expand_prompt(wake_req, wake_req.message or "", handoff_contract="none")
        is False
    )

    mech_req = CursorDispatchRequest(
        thread_id="10479",
        model="cursor/composer-2.5",
        dispatch_id="auto-mech",
        execution_id="exec-mech",
        message="mechanical packet",
        handoff_contract="pure-mechanical",
        operator_contract="pure-mechanical",
        continuity_root_thread_id="10479",
    )
    assert (
        giw_should_expand_prompt(
            mech_req,
            mech_req.message or "",
            handoff_contract="pure-mechanical",
        )
        is False
    )


@pytest.fixture
def client(tmp_path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    CursorDispatchLedger._instance = None
    from services.git_integration_worker.routes import cursor_sdk as route_mod

    monkeypatch.setattr(
        route_mod,
        "validate_dispatch_context",
        lambda *_a, **_k: {"mcp_server": "vortex"},
    )
    monkeypatch.setattr(
        route_mod,
        "capture_wt_baseline_with_hashes",
        lambda *_a, **_k: {"admit_head": "deadbeef", "files": {}},
    )
    return TestClient(create_app())


@patch(
    "services.git_integration_worker.admission.WorkAdmissionController.create_tracked_task",
    return_value=MagicMock(done=lambda: False),
)
def test_cursor_auto_enrolled_sketch_admit_records_prompt_expand_pending(
    _mock_task: MagicMock,
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC3 — cursor-auto-shaped admit with parent 10479 + sketch reaches prelude."""
    from services.git_integration_worker.routes import cursor_sdk as route_mod

    expand_calls: list[tuple[str, dict[str, str]]] = []

    def _fake_expand(task: str, options: dict[str, str], **_kwargs: object) -> ExpandRun:
        expand_calls.append((task, options))
        return ExpandRun(ok=True, prompt="TASK'", execution_id="exp-admit-test")

    monkeypatch.setattr(
        "systems.frontier_consult.prompt_expand_prelude.run_prompt_expand",
        _fake_expand,
    )
    from services.git_integration_worker.cursor_sdk_closeout import SdkRunOutcome

    monkeypatch.setattr(
        route_mod,
        "_run_sdk_sync",
        lambda **_k: SdkRunOutcome(
            body="done",
            status="finished",
            duration_ms=1,
            tool_call_count=0,
            sdk_request_id="sdk-test",
            request_id_source="stream",
        ),
    )

    body = {
        "thread_id": "11767",
        "model": "cursor/composer-2.5",
        "dispatch_id": "auto-expand-admit",
        "execution_id": "exec-expand-admit",
        "message": "# Next-Seat Dispatch Packet\n\nWire prompt-expand into GIW.",
        "handoff_contract": "pure-mechanical",
        "operator_contract": "sketch",
        "continuity_root_thread_id": "10479",
        "parent_dispatch_thread_id": "10479",
        "admitted_via": "cursor-auto",
    }
    resp = client.post("/api/v1/cursor/dispatch", json=body)
    assert resp.status_code == 200, resp.text
    assert resp.json().get("prompt_expand") == "pending"
