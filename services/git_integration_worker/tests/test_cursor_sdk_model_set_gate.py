"""GIW model-set gate and re-admit refusals (AC 8–9', T7)."""

from __future__ import annotations

import inspect
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from services.git_integration_worker.admission import WorkAdmissionController
from services.git_integration_worker.config import load_config
from services.git_integration_worker.cursor_dispatch_ledger import CursorDispatchLedger
from services.git_integration_worker.cursor_sdk_park_ledger import mark_parked
from services.git_integration_worker.cursor_sdk_park_resume import resume_parked_dispatches
from services.git_integration_worker.models.cursor_api import (
    CursorDispatchRequest,
    CursorDispatchResponse,
)
from services.git_integration_worker.routes import cursor_sdk as route_mod
from services.git_integration_worker.app import create_app
from services.git_integration_worker.tests.test_cursor_sdk_route import _dispatch_body


@pytest.fixture
def client() -> TestClient:
    return TestClient(create_app())


@pytest.fixture(autouse=True)
def _isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("CURSOR_DISPATCH_HOME_ROOT", str(tmp_path / "homes"))
    CursorDispatchLedger._instance = None
    yield
    CursorDispatchLedger._instance = None


@pytest.fixture(autouse=True)
def _route_stubs(monkeypatch: pytest.MonkeyPatch) -> None:
    from services.git_integration_worker.cursor_sdk_context import (
        CursorApiKeyResolution,
    )

    monkeypatch.setattr(
        route_mod,
        "validate_dispatch_context",
        lambda *_a, **_k: {"setting_sources": ["user"], "mcp_server": "vortex"},
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


def test_admit_cursor_dispatch_has_no_enforce_model_set_kwarg() -> None:
    sig = inspect.signature(route_mod.admit_cursor_dispatch)
    assert "enforce_model_set" not in sig.parameters


def test_enforce_model_set_absent_in_production_giw() -> None:
    root = Path(__file__).resolve().parents[1]
    hits = []
    for path in root.rglob("*.py"):
        if "tests" in path.parts:
            continue
        text = path.read_text(encoding="utf-8")
        if "enforce_model_set" in text:
            hits.append(str(path.relative_to(root)))
    assert hits == []


def test_http_refuses_argon_prefixed(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    bridge = MagicMock()
    monkeypatch.setattr(route_mod, "launch_sdk_bridge", bridge)
    resp = client.post(
        "/api/v1/cursor/dispatch",
        json=_dispatch_body(model="cursor/gemini-4-argon"),
    )
    assert resp.status_code == 422
    payload = resp.json()
    assert payload["code"] == "CURSOR_MODEL_UNTRUSTED"
    data = payload.get("data") or {}
    assert data.get("validation_stage") == "model_set"
    assert payload.get("retryable") is False
    assert "cursor/composer-2.5" in (data.get("fix_hint") or "")
    bridge.assert_not_called()


def test_http_refuses_bare_argon(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    bridge = MagicMock()
    monkeypatch.setattr(route_mod, "launch_sdk_bridge", bridge)
    resp = client.post(
        "/api/v1/cursor/dispatch",
        json=_dispatch_body(model="gemini-4-argon"),
    )
    assert resp.status_code == 422
    assert resp.json()["code"] == "CURSOR_MODEL_UNTRUSTED"
    bridge.assert_not_called()


def _bus() -> AsyncMock:
    bus = AsyncMock()
    bus.reply = AsyncMock(return_value=MagicMock(status_code=201, body={"turn_number": 1}))
    bus.terminate_dispatch = AsyncMock(return_value=MagicMock(status_code=200, body={}))
    return bus


def _seed_parked_non_member(
    dispatch_id: str, *, tmp_path: Path, thread_id: str = "22144"
) -> None:
    ledger = CursorDispatchLedger.instance()
    req = CursorDispatchRequest(
        thread_id=thread_id,
        model="cursor/gemini-4-argon",
        dispatch_id=dispatch_id,
        execution_id=f"exec-{dispatch_id}",
        caller_agent="cursor",
        message="parked argon",
    )
    ledger.admit(
        req=req,
        fingerprint=ledger.fingerprint(req),
        execution_id=req.execution_id,
        caller_agent="cursor",
        resolved_model="gemini-4-argon",
        admission=CursorDispatchResponse(
            admitted=True, dispatch_id=dispatch_id, thread_id=thread_id, model_id="m"
        ),
        contract="none",
        source_repo=str(load_config().source_repo),
        lease_key=str(load_config().source_repo),
    )
    ledger.merge_record_json(
        dispatch_id=dispatch_id,
        patch={"model": "cursor/gemini-4-argon"},
    )
    ledger.mark_running(dispatch_id=dispatch_id)
    ledger.record_sdk_identity(
        dispatch_id=dispatch_id,
        agent_id=f"agent-{dispatch_id}",
        run_id="r",
    )
    store = tmp_path / f"store-{dispatch_id}"
    store.mkdir(parents=True, exist_ok=True)
    (store / "index.db").write_text("x", encoding="utf-8")
    ledger.record_state_root(dispatch_id=dispatch_id, state_root=str(store))
    mark_parked(
        dispatch_id=dispatch_id,
        intent_id="intent-ms",
        drain_epoch=1,
        actor="manage",
        reason="deploy",
        requested_at="x",
        method="run_cancel",
        tool_call_count=0,
        last_tool_calls=[],
        sidecar_uri=None,
    )
    parked_at = datetime.now(UTC) - timedelta(hours=2)
    with ledger._connect() as conn:
        conn.execute(
            "UPDATE cursor_sdk_dispatches SET parked_at=?, park_expires_at=? WHERE dispatch_id=?",
            (
                parked_at.isoformat(),
                (parked_at + timedelta(days=1)).isoformat(),
                dispatch_id,
            ),
        )


@pytest.mark.asyncio
async def test_park_resume_refuses_stored_non_member(tmp_path: Path) -> None:
    _seed_parked_non_member("p-argon", tmp_path=tmp_path)
    started = (datetime.now(UTC) - timedelta(hours=1)).isoformat()
    summary = await resume_parked_dispatches(
        cfg=load_config(),
        controller=WorkAdmissionController(
            ledger=CursorDispatchLedger.instance(),
            worker_id="w",
            pid=0,
            worker_started_at=started,
        ),
        code_version="gate-test",
        bus=_bus(),
    )
    assert ("p-argon", "CURSOR_MODEL_UNTRUSTED") in summary.refused
    child_id = "p-argon-r1"
    with CursorDispatchLedger.instance()._connect() as conn:
        row = conn.execute(
            "SELECT dispatch_id FROM cursor_sdk_dispatches WHERE dispatch_id=?",
            (child_id,),
        ).fetchone()
    assert row is None


def test_member_bare_composer_not_model_set_refused(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Failure after model_set is acceptable; must not be model_set."""
    bridge = MagicMock()
    monkeypatch.setattr(route_mod, "launch_sdk_bridge", bridge)
    resp = client.post(
        "/api/v1/cursor/dispatch",
        json=_dispatch_body(model="composer-2.5", dispatch_id="disp-member-bare"),
    )
    if resp.status_code == 422:
        data = resp.json().get("data") or {}
        assert data.get("validation_stage") != "model_set"
