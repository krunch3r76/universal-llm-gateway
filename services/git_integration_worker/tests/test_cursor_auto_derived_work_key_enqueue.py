"""Parallel-by-default child lanes — derived work_key at cursor-auto enqueue."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from services.git_integration_worker.app import create_app
from services.git_integration_worker.cursor_auto.execution_mode import (
    ISOLATED_LANE_CONDUCTOR_MODE,
)
from services.git_integration_worker.cursor_auto.job_ledger import AutoJobLedger
from services.git_integration_worker.cursor_auto.liveness import get_registry
from services.git_integration_worker.cursor_auto.queue import (
    get_queue,
    reset_queue_for_tests,
)
from services.git_integration_worker.cursor_auto.queue_health_events import (
    reset_rising_edge_state_for_tests,
)


@pytest.fixture(autouse=True)
def _isolated_auto_ledger(tmp_path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("CURSOR_SDK_DISPATCH_LEDGER", raising=False)
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    AutoJobLedger.reset_for_tests()
    reset_queue_for_tests(durable=True)
    reset_rising_edge_state_for_tests()
    yield
    AutoJobLedger.reset_for_tests()
    reset_rising_edge_state_for_tests()


def _base_payload(**overrides):
    body = {
        "thread_id": "13061-child",
        "turn_number": 1,
        "subject": "derived work_key enqueue",
        "body": (
            "TYPE: DIRECTIVE\n"
            "files_expected: services/git_integration_worker/routes/cursor_auto.py\n"
            "scope: parallel child lane\n"
        ),
        "from_agent": "cursor-auto",
        "to_agent": "cursor",
        "desired_model": "auto",
        "desired_effort": "medium",
        "contract": "implement",
        "parent_thread": "12286",
        "lane_role": "sub_mission",
    }
    body.update(overrides)
    return body


@pytest.fixture
def client(tmp_path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv(
        "CURSOR_AUTO_HOP_WATCHES_PATH",
        str(tmp_path / "hop_cadence_watches.json"),
    )
    return TestClient(create_app())


def test_sub_mission_child_omitted_work_key_isolated_derived(
    client: TestClient,
) -> None:
    get_registry().register("13061-child-handler")
    with patch(
        "services.git_integration_worker.routes.cursor_auto.supersede_same_thread_inflight",
        new=AsyncMock(return_value=None),
    ):
        resp = client.post("/api/v1/git/cursor-auto/enqueue", json=_base_payload())
    assert resp.status_code == 200
    data = resp.json()
    admission = data["job_admission"]
    assert admission["execution_mode"] == ISOLATED_LANE_CONDUCTOR_MODE
    assert admission["work_key"] == "agent-bus:13061-child (source=derived)"
    assert admission["work_key_source"] == "derived"
    job = get_queue()._jobs[data["job_id"]]
    assert job.work_key == "agent-bus:13061-child"
    assert job.work_key_source == "derived"


def test_same_thread_rerequest_serial_same_thread(client: TestClient) -> None:
    get_registry().register("13061-same-thread-handler")
    queue = get_queue()
    queue.enqueue(
        thread_id="13061-same",
        turn_number=1,
        subject="first",
        body="TYPE: DIRECTIVE\nfiles_expected: x\n",
        from_agent="cursor-auto",
        to_agent="cursor",
        desired_model="auto",
        desired_effort="medium",
        contract="implement",
        lane="B",
        work_key="agent-bus:13061-same",
        work_key_source="derived",
        execution_mode=ISOLATED_LANE_CONDUCTOR_MODE,
    )
    claimed = queue.claim_next_concurrent()
    assert claimed is not None
    payload = _base_payload(
        thread_id="13061-same",
        parent_thread="12286",
        lane_role="sub_mission",
    )
    with patch(
        "services.git_integration_worker.routes.cursor_auto.supersede_same_thread_inflight",
        new=AsyncMock(return_value=None),
    ):
        resp = client.post("/api/v1/git/cursor-auto/enqueue", json=payload)
    assert resp.status_code == 200
    admission = resp.json()["job_admission"]
    assert admission["execution_mode"] == "serial"
    assert admission["serial_reason"] == "same_thread"


def test_lane_a_serial_reason(client: TestClient) -> None:
    get_registry().register("13061-lane-a-handler")
    payload = _base_payload(thread_id="13061-lane-a", lane="A")
    with patch(
        "services.git_integration_worker.routes.cursor_auto.supersede_same_thread_inflight",
        new=AsyncMock(return_value=None),
    ):
        resp = client.post("/api/v1/git/cursor-auto/enqueue", json=payload)
    assert resp.status_code == 200
    admission = resp.json()["job_admission"]
    assert admission["execution_mode"] == "serial"
    assert admission["serial_reason"] == "lane_a"


def test_same_work_key_active_serial_reason(client: TestClient) -> None:
    get_registry().register("13061-wk-handler")
    queue = get_queue()
    queue.enqueue(
        thread_id="13061-peer",
        turn_number=1,
        subject="holder",
        body="TYPE: DIRECTIVE\nfiles_expected: x\n",
        from_agent="cursor-auto",
        to_agent="cursor",
        desired_model="auto",
        desired_effort="medium",
        contract="implement",
        lane="B",
        work_key="todo:shared-wk",
        work_key_source="wire",
        execution_mode=ISOLATED_LANE_CONDUCTOR_MODE,
    )
    assert queue.claim_next_concurrent() is not None
    payload = _base_payload(
        thread_id="13061-wk-child",
        work_key="todo:shared-wk",
    )
    with patch(
        "services.git_integration_worker.routes.cursor_auto.supersede_same_thread_inflight",
        new=AsyncMock(return_value=None),
    ):
        resp = client.post("/api/v1/git/cursor-auto/enqueue", json=payload)
    assert resp.status_code == 200
    admission = resp.json()["job_admission"]
    assert admission["execution_mode"] == "serial"
    assert admission["serial_reason"] == "same_work_key_active"
