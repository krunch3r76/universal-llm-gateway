"""R8 bind §4 probe 2 — two-row mechanical conductor hop acceptance.

Hermetic integration: predecessor terminal → reactor → successor admit through
the real GIW dispatch route (occupancy gates exercised). Scratch todo only.
"""

from __future__ import annotations

import json
import subprocess
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from services.git_integration_worker.app import create_app
from services.git_integration_worker.config import WorkerConfig
from services.git_integration_worker.cursor_dispatch_ledger import CursorDispatchLedger
from services.git_integration_worker.cursor_sdk_closeout.closeout_records import (
    SdkRunOutcome,
)
from services.git_integration_worker.cursor_sdk_closeout.conductor_hop import (
    maybe_fire_conductor_hop_reactor,
    merge_conductor_closeout_hop_authority,
)
from services.git_integration_worker.cursor_sdk_closeout.implement_body import (
    build_implement_closeout_body,
)
from services.git_integration_worker.cursor_sdk_ledger_hop import (
    hop_fields_from_record_json,
)
from services.git_integration_worker.cursor_sdk_packet import resolve_prompt_preamble
from services.git_integration_worker.models.cursor_api import (
    CursorDispatchRequest,
    CursorDispatchResponse,
)

pytestmark = pytest.mark.offline

_THREAD_ID = "probe2-worker-thread"
_WORK_KEY = "todo:conductor-hop-probe2-scratch"
_LANE_BRANCH = "cursor-sdk/lane-probe2"
_ROW_HOP_CLOSEOUT = """\
status: complete
stop: ROW_HOP
**hop_seq:** 1
"""
_CONDUCTOR_PACKET = (
    "---\n"
    "packet_kind: conductor\n"
    f"source_ref: {_WORK_KEY}\n"
    "contract: light-bounded\n"
    "lane: B\n"
    "summon_mode: confer-and-finish\n"
    "summoning_thread_id: 9638\n"
    "---\n"
    "Use the conductor skill.\n"
    "<scope>Probe 2 scratch mission.</scope>\n"
)


def _git(*args: str, cwd: Path) -> None:
    subprocess.run(["git", *args], cwd=str(cwd), check=True, capture_output=True)


@pytest.fixture(autouse=True)
def _isolated_ledger(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    CursorDispatchLedger._instance = None
    yield
    CursorDispatchLedger._instance = None


@pytest.fixture
def git_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "source_repo"
    repo.mkdir()
    _git("init", "-b", "master", cwd=repo)
    _git("config", "user.email", "probe2@example.com", cwd=repo)
    _git("config", "user.name", "probe2", cwd=repo)
    (repo / "README.md").write_text("probe2\n", encoding="utf-8")
    _git("add", "README.md", cwd=repo)
    _git("commit", "-m", "seed", cwd=repo)
    return repo


@pytest.fixture
def worker_cfg(tmp_path: Path, git_repo: Path) -> WorkerConfig:
    wt_root = tmp_path / "worktrees"
    wt_root.mkdir()
    dispatch_ws = tmp_path / "dispatch_ws"
    dispatch_ws.mkdir()
    return WorkerConfig(
        host="127.0.0.1",
        port=8091,
        source_repo=git_repo,
        worktree_root=wt_root,
        dispatch_workspace=dispatch_ws,
        green_gate_cmd=["true"],
    )


@pytest.fixture
def giw_client(
    worker_cfg: WorkerConfig,
    monkeypatch: pytest.MonkeyPatch,
) -> TestClient:
    monkeypatch.setenv("GIT_INTEGRATION_SOURCE_REPO", str(worker_cfg.source_repo))
    monkeypatch.setenv("GIT_INTEGRATION_WORKTREE_ROOT", str(worker_cfg.worktree_root))
    monkeypatch.setenv(
        "GIT_INTEGRATION_DISPATCH_WORKSPACE", str(worker_cfg.dispatch_workspace)
    )
    from services.git_integration_worker.routes import cursor_sdk as route_mod

    monkeypatch.setattr(route_mod, "_CONFIG", worker_cfg)
    monkeypatch.setattr(
        route_mod,
        "validate_dispatch_context",
        lambda _repo: {"setting_sources": ["projectSettings"]},
    )

    async def _noop_acquire(**kwargs: object) -> str:
        return str(kwargs.get("dispatch_id") or "slot")

    monkeypatch.setattr(route_mod, "acquire_sdk_dispatch_slot", _noop_acquire)
    monkeypatch.setattr(
        route_mod, "release_or_restore_for_child_sync", lambda *_a, **_k: "released"
    )
    monkeypatch.setattr(route_mod, "emit_implement_closeout_trigger", MagicMock())

    app = create_app()
    app.state.worker_config = worker_cfg
    return TestClient(app)


def _parse_iso(ts: str | None) -> datetime | None:
    if not ts:
        return None
    return datetime.fromisoformat(str(ts))


def _ledger_rows_for_thread(thread_id: str) -> list[dict[str, Any]]:
    ledger = CursorDispatchLedger.instance()
    with ledger._connect() as conn:
        rows = conn.execute(
            "SELECT * FROM cursor_sdk_dispatches WHERE thread_id=? "
            "ORDER BY COALESCE(json_extract(record_json, '$.hop_seq'), 0), started_at",
            (thread_id,),
        ).fetchall()
    return [{k: row[k] for k in row.keys()} for row in rows]


def _admit_predecessor(
    ledger: CursorDispatchLedger,
    *,
    dispatch_id: str,
    execution_id: str,
) -> dict[str, Any]:
    req = CursorDispatchRequest(
        thread_id=_THREAD_ID,
        model="cursor/composer-2.5",
        dispatch_id=dispatch_id,
        execution_id=execution_id,
        message=_CONDUCTOR_PACKET,
        handoff_contract="light-bounded",
        source_ref=_WORK_KEY,
        lane="B",
        hop_seq=1,
        hop_from="spawn-root",
        hop_reason="spawn",
    )
    ledger.admit(
        req=req,
        fingerprint=ledger.fingerprint(req),
        execution_id=execution_id,
        caller_agent="cursor",
        resolved_model="composer-2.5",
        admission=CursorDispatchResponse(
            admitted=True,
            dispatch_id=dispatch_id,
            thread_id=_THREAD_ID,
            model_id="composer-2.5",
        ),
        contract="light-bounded",
        source_repo="/repo/lane-probe2",
        lease_key="/repo/lane-probe2",
        work_key=_WORK_KEY,
        source_ref=_WORK_KEY,
        hop_seq=1,
        hop_from="spawn-root",
        hop_reason="spawn",
    )
    ledger.merge_record_json(
        dispatch_id=dispatch_id,
        patch={
            "packet_kind": "conductor",
            "lane": "B",
            "lane_branch": _LANE_BRANCH,
            "source_ref": _WORK_KEY,
            "summon_mode": "confer_and_finish",
            "summoning_thread_id": "9638",
            "generation_options": {"summon_mode": "confer_and_finish"},
        },
    )
    with ledger._connect() as conn:
        row = conn.execute(
            "SELECT * FROM cursor_sdk_dispatches WHERE dispatch_id=?",
            (dispatch_id,),
        ).fetchone()
    assert row is not None
    return {k: row[k] for k in row.keys()}


def _hop_dispatch_payload(body: dict[str, Any]) -> dict[str, Any]:
    successor_id = f"succ-{uuid.uuid4().hex[:12]}"
    exec_id = f"exec-{successor_id}"
    model = body.get("model") or "cursor/composer-2.5"
    assert str(model).startswith("cursor/"), (
        f"team_dispatch relay must forward prefixed model verbatim, got {model!r}"
    )
    return {
        "thread_id": body["reuse_thread"],
        "model": model,
        "dispatch_id": successor_id,
        "execution_id": exec_id,
        "handoff_contract": body.get("contract") or "light-bounded",
        "caller_agent": body.get("caller_agent"),
        "source_ref": body["source_ref"],
        "lane": body.get("lane") or "B",
        "hop_seq": body["hop_seq"],
        "hop_from": body["hop_from"],
        "hop_reason": body["hop_reason"],
        "message": _CONDUCTOR_PACKET,
    }


@pytest.mark.asyncio
@patch(
    "services.git_integration_worker.admission.WorkAdmissionController.create_tracked_task",
    return_value=MagicMock(done=lambda: False),
)
async def test_probe2_two_row_mechanical_conductor_mission(
    _mock_task: MagicMock,
    giw_client: TestClient,
) -> None:
    """Bind §4 probe 2 pass signal on scratch todo (axis-A falsifier must not fire)."""
    ledger = CursorDispatchLedger.instance()
    predecessor_id = "probe2-pred-1"
    pred_exec = "exec-probe2-pred-1"
    _admit_predecessor(
        ledger,
        dispatch_id=predecessor_id,
        execution_id=pred_exec,
    )
    ledger.mark_running(dispatch_id=predecessor_id)
    merge_conductor_closeout_hop_authority(
        dispatch_id=predecessor_id,
        closeout_body=_ROW_HOP_CLOSEOUT,
        thread_id=_THREAD_ID,
    )
    ledger.mark_terminal(dispatch_id=predecessor_id, terminal_status="completed")

    admitted_events: list[dict[str, Any]] = []
    relay_bodies: list[dict[str, Any]] = []

    async def _stargate_relay(body: dict[str, Any], **_kwargs: object) -> tuple[bool, dict]:
        relay_bodies.append(body)
        payload = _hop_dispatch_payload(body)
        resp = giw_client.post("/api/v1/cursor/dispatch", json=payload)
        if resp.status_code >= 400:
            return False, {
                "status_code": resp.status_code,
                "error": resp.json(),
            }
        data = resp.json()
        return True, {
            "dispatch_id": data.get("dispatch_id") or payload["dispatch_id"],
            "execution_id": payload["execution_id"],
        }

    with patch(
        "services.git_integration_worker.cursor_sdk_closeout.conductor_hop.post_conductor_hop_team_dispatch",
        side_effect=_stargate_relay,
    ):
        with patch(
            "services.git_integration_worker.cursor_sdk_closeout.conductor_hop.emit_frontier_sdk_conductor_hop_admitted",
            side_effect=lambda **kw: admitted_events.append(kw),
        ):
            await maybe_fire_conductor_hop_reactor(dispatch_id=predecessor_id)
            await maybe_fire_conductor_hop_reactor(dispatch_id=predecessor_id)

    rows = _ledger_rows_for_thread(_THREAD_ID)
    conductor_rows = [
        r
        for r in rows
        if json.loads(r.get("record_json") or "{}").get("packet_kind") == "conductor"
    ]
    assert len(conductor_rows) == 2, (
        f"expected two conductor ledger rows, got {len(conductor_rows)}"
    )

    pred = next(r for r in conductor_rows if r["dispatch_id"] == predecessor_id)
    succ = next(r for r in conductor_rows if r["dispatch_id"] != predecessor_id)

    pred_fields = hop_fields_from_record_json(pred["record_json"])
    succ_fields = hop_fields_from_record_json(succ["record_json"])
    assert pred_fields.get("hop_seq") == 1
    assert succ_fields.get("hop_seq") == 2
    assert succ_fields.get("hop_from") == predecessor_id
    assert pred_fields.get("hop_successor") == succ["dispatch_id"]

    pred_terminal = _parse_iso(pred.get("terminal_at"))
    succ_started = _parse_iso(succ.get("started_at"))
    assert pred_terminal is not None and succ_started is not None
    assert pred_terminal <= succ_started, (
        "predecessor must complete before successor admit (started_at)"
    )

    pred_rec = json.loads(pred["record_json"])
    succ_rec = json.loads(succ["record_json"])
    assert pred_rec.get("lane") == "B"
    assert succ_rec.get("lane") == "B"

    preamble = resolve_prompt_preamble(
        handoff_contract="light-bounded",
        prompt_preamble=None,
        inferred_contract="light-bounded",
        lane="B",
        lane_branch=_LANE_BRANCH,
        dispatch_id=succ["dispatch_id"],
        has_packet_path=True,
        thread_id=_THREAD_ID,
        hop_seq=succ_fields.get("hop_seq"),
        hop_from=succ_fields.get("hop_from"),
        hop_reason=succ_fields.get("hop_reason"),
        existing_text=_CONDUCTOR_PACKET,
    )
    assert succ["dispatch_id"] in preamble
    assert f"nest_under={succ['dispatch_id']}" in preamble
    assert f"hop {succ_fields['hop_seq']}" in preamble.lower()

    assert len(admitted_events) == 1, "hop.admitted must fire once (idempotency)"
    assert admitted_events[0]["predecessor_dispatch_id"] == predecessor_id
    assert admitted_events[0]["successor_dispatch_id"] == succ["dispatch_id"]
    assert admitted_events[0]["hop_seq"] == 2
    assert len(relay_bodies) == 1
    assert relay_bodies[0]["model"] == "cursor/composer-2.5"
    assert relay_bodies[0]["dispatch_thread_id"] == "9638"
    assert relay_bodies[0]["reuse_thread"] == _THREAD_ID
    assert relay_bodies[0]["generation_options"]["summon_mode"] == "confer_and_finish"


def test_probe2_production_closeout_json_has_no_row_hop_tokens() -> None:
    outcome = SdkRunOutcome(
        body=_ROW_HOP_CLOSEOUT,
        status="finished",
        duration_ms=500,
        tool_call_count=2,
    )
    json_body = build_implement_closeout_body(
        dispatch_id="probe2-json-check",
        outcome=outcome,
        degraded_reason="conductor_row_hop",
        sidecar_ref="workspaces://x/probe2.md",
        result_bytes=120,
        thread_id=_THREAD_ID,
        work_item_ref=_WORK_KEY,
    )
    assert "ROW_HOP" not in json_body


@pytest.mark.asyncio
async def test_probe2_axis_a_falsifier_not_triggered_when_predecessor_terminal() -> None:
    """Axis-A falsifier: no occupancy refusal while predecessor reads completed."""
    ledger = CursorDispatchLedger.instance()
    predecessor_id = "probe2-falsifier-pred"
    _admit_predecessor(
        ledger,
        dispatch_id=predecessor_id,
        execution_id="exec-falsifier-pred",
    )
    merge_conductor_closeout_hop_authority(
        dispatch_id=predecessor_id,
        closeout_body=_ROW_HOP_CLOSEOUT,
        thread_id=_THREAD_ID,
    )
    ledger.mark_terminal(dispatch_id=predecessor_id, terminal_status="completed")

    with patch(
        "services.git_integration_worker.cursor_sdk_closeout.conductor_hop.post_conductor_hop_team_dispatch",
        AsyncMock(return_value=(True, {"dispatch_id": "probe2-falsifier-succ"})),
    ):
        await maybe_fire_conductor_hop_reactor(dispatch_id=predecessor_id)

    with ledger._connect() as conn:
        pred = conn.execute(
            "SELECT terminal_status, record_json FROM cursor_sdk_dispatches "
            "WHERE dispatch_id=?",
            (predecessor_id,),
        ).fetchone()
    assert pred is not None
    assert pred["terminal_status"] == "completed"
    fields = hop_fields_from_record_json(pred["record_json"])
    assert fields.get("hop_successor") == "probe2-falsifier-succ"
    assert "hop_admit_error" not in fields
