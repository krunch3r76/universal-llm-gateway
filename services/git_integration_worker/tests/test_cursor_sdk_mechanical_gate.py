"""GIW mechanical executor gate at admit (wider-find AC 1–6, 10, 12)."""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from services.git_integration_worker.admission import WorkAdmissionController
from services.git_integration_worker.app import create_app
from services.git_integration_worker.cursor_dispatch_ledger import CursorDispatchLedger
from services.git_integration_worker.models.cursor_api import CursorDispatchRequest
from services.git_integration_worker.routes import cursor_sdk as route_mod
from services.git_integration_worker.tests.test_cursor_sdk_route import _dispatch_body

from implement_admission.mechanical_executor_gate import IMPLEMENT_COMPOSER_WAIVER_MARKER


@pytest.fixture
def client(tmp_path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("CURSOR_DISPATCH_HOME_ROOT", str(tmp_path / "homes"))
    CursorDispatchLedger._instance = None
    from services.git_integration_worker.cursor_sdk_context import CursorApiKeyResolution

    monkeypatch.setattr(
        route_mod,
        "validate_dispatch_context",
        lambda *_a, **_k: {"mcp_server": "vortex"},
    )
    monkeypatch.setattr(
        route_mod,
        "resolve_cursor_api_key",
        lambda *_a, **_k: CursorApiKeyResolution(provenance="env:CURSOR_API_KEY"),
    )
    monkeypatch.setattr(
        route_mod, "capture_wt_baseline_with_hashes", lambda *_a, **_k: {"files": {}}
    )
    monkeypatch.setattr(
        WorkAdmissionController,
        "create_tracked_task",
        MagicMock(return_value=MagicMock(done=lambda: False)),
    )
    monkeypatch.setattr(
        "implement_admission.cursor_sdk_other_models_pool."
        "load_cursor_sdk_other_models_pool_mode",
        lambda *_a, **_k: "deny",
    )
    return TestClient(create_app())


def _ledger_count(tmp_path) -> int:
    ledger = CursorDispatchLedger.instance()
    with ledger._connect() as conn:
        return conn.execute("SELECT COUNT(*) FROM cursor_sdk_dispatches").fetchone()[0]


def test_grok_implement_refused_contract_model(
    client: TestClient, tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bridge = MagicMock()
    monkeypatch.setattr(route_mod, "launch_sdk_bridge", bridge)
    body = _dispatch_body(
        model="cursor/grok-4.7",
        handoff_contract="implement",
        dispatch_id="mech-gate-1",
        execution_id="exec-mech-gate-1",
    )
    before = _ledger_count(tmp_path)
    resp = client.post("/api/v1/cursor/dispatch", json=body)
    assert resp.status_code == 422
    payload = resp.json()
    assert payload.get("data", {}).get("validation_stage") == "contract_model"
    assert _ledger_count(tmp_path) == before
    bridge.assert_not_called()


def test_operator_implement_pure_mechanical_handoff_refused(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(route_mod, "launch_sdk_bridge", MagicMock())
    body = _dispatch_body(
        model="cursor/grok-4.7",
        handoff_contract="pure-mechanical",
        operator_contract="implement",
        dispatch_id="mech-gate-2",
        execution_id="exec-mech-gate-2",
    )
    resp = client.post("/api/v1/cursor/dispatch", json=body)
    assert resp.status_code == 422
    assert resp.json().get("data", {}).get("validation_stage") == "contract_model"


def test_inferred_implement_from_packet_refused(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(route_mod, "launch_sdk_bridge", MagicMock())
    body = _dispatch_body(
        model="cursor/grok-4.7",
        message="---\ncontract: implement\n---\n\nDo work.\n",
        dispatch_id="mech-gate-3",
        execution_id="exec-mech-gate-3",
    )
    resp = client.post("/api/v1/cursor/dispatch", json=body)
    assert resp.status_code == 422
    assert resp.json().get("data", {}).get("validation_stage") == "contract_model"


def test_admitted_via_stargate_still_refused(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(route_mod, "launch_sdk_bridge", MagicMock())
    body = _dispatch_body(
        model="cursor/grok-4.7",
        handoff_contract="implement",
        admitted_via="stargate",
        dispatch_id="mech-gate-4",
        execution_id="exec-mech-gate-4",
    )
    resp = client.post("/api/v1/cursor/dispatch", json=body)
    assert resp.status_code == 422


def test_confer_grok_still_admits(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(route_mod, "launch_sdk_bridge", MagicMock())
    body = _dispatch_body(
        model="cursor/grok-4.7",
        handoff_contract="confer",
        dispatch_id="mech-gate-5",
        execution_id="exec-mech-gate-5",
    )
    resp = client.post("/api/v1/cursor/dispatch", json=body)
    assert resp.status_code != 422 or resp.json().get("data", {}).get("validation_stage") != "contract_model"


def test_waiver_full_trio_admits(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(route_mod, "launch_sdk_bridge", MagicMock())
    body = _dispatch_body(
        model="cursor/grok-4.7",
        handoff_contract="implement",
        ban_override=True,
        ban_override_reason=f"audit {IMPLEMENT_COMPOSER_WAIVER_MARKER}",
        model_ban_override_event_id="evt-waiver-1",
        dispatch_id="mech-gate-6",
        execution_id="exec-mech-gate-6",
    )
    resp = client.post("/api/v1/cursor/dispatch", json=body)
    assert resp.status_code != 422 or resp.json().get("data", {}).get("validation_stage") != "contract_model"


@pytest.mark.asyncio
async def test_resume_grok_parent_child_not_contract_model_refused(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from services.git_integration_worker.config import load_config

    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    CursorDispatchLedger._instance = None
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_sdk_model_ban._parent_resolved_model",
        lambda _pid: "cursor/grok-4.7",
    )
    monkeypatch.setattr(route_mod, "launch_sdk_bridge", MagicMock())
    controller = WorkAdmissionController(
        ledger=CursorDispatchLedger.instance(),
        worker_id="w",
        pid=0,
        worker_started_at="2026-01-01T00:00:00Z",
    )
    child = CursorDispatchRequest(
        thread_id="24234",
        model="cursor/grok-4.7",
        dispatch_id="child-grok-resume",
        execution_id="exec-child",
        message="child",
        handoff_contract="implement",
        resume_of="parent-grok",
    )
    resp = await route_mod.admit_cursor_dispatch(
        child, cfg=load_config(), controller=controller
    )
    if resp.status_code == 422:
        data = json.loads(bytes(resp.body).decode()).get("data") or {}
        assert data.get("validation_stage") != "contract_model"


def test_cursor_auto_implement_refused_contract_model(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(route_mod, "launch_sdk_bridge", MagicMock())
    body = _dispatch_body(
        model="cursor/auto",
        handoff_contract="implement",
        dispatch_id="mech-gate-auto",
        execution_id="exec-mech-gate-auto",
    )
    resp = client.post("/api/v1/cursor/dispatch", json=body)
    assert resp.status_code == 422
    assert resp.json().get("data", {}).get("validation_stage") == "contract_model"