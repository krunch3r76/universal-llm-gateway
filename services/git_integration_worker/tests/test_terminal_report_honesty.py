"""Arc 6655 — terminal report honesty: mark_done gating + propagate spill."""

from __future__ import annotations

import asyncio
import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from agent_bus_store.body_auto_spill import prepare_body_for_insert
from services.git_integration_worker.cursor_auto.handler_terminal import (
    post_terminal_status,
)
from services.git_integration_worker.cursor_auto.job_lifecycle import (
    PHASE_TERMINAL_DONE,
    PHASE_TERMINAL_FAILED,
    PHASE_TERMINAL_REPORT_UNDELIVERED,
    terminal_phase_for_status,
)
from services.git_integration_worker.cursor_auto.job_ledger import (
    AutoJobLedger,
    get_ledger,
)
from services.git_integration_worker.cursor_auto.propagation_terminal_payload import (
    compact_propagate_terminal_payload,
)
from services.git_integration_worker.cursor_auto.queue import (
    AutoJob,
    AutoJobQueue,
    get_queue,
    reset_queue_for_tests,
)
from services.git_integration_worker.cursor_auto.terminal_post_outcome import (
    STATUS_REPORT_UNDELIVERED,
    terminal_post_delivered,
    terminal_post_permanent_reject,
    terminal_post_retryable,
    terminal_reason_for_status,
)


@pytest.fixture(autouse=True)
def _isolated_ledger(tmp_path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    AutoJobLedger.reset_for_tests()
    reset_queue_for_tests(durable=True)
    yield
    AutoJobLedger.reset_for_tests()


def _job() -> AutoJob:
    return AutoJob(
        job_id="job-honesty",
        thread_id="6655",
        turn_number=1297,
        subject="propagate restart",
        body="TYPE: DIRECTIVE\ncontract: propagate\n",
        from_agent="cdp-operator-6655",
        to_agent="cursor",
        desired_model="auto",
        desired_effort="medium",
        contract="propagate",
        request_id="req-6655-1297",
    )


def _large_proof(chars: int = 25_000) -> dict[str, str]:
    return {"artifact": "x" * chars}


class _RecordingQueue:
    def __init__(self) -> None:
        self.done: list[tuple[str, bool]] = []
        self.undelivered: list[dict[str, Any]] = []

    def mark_done(self, job_id: str, *, failed: bool = False) -> None:
        self.done.append((job_id, failed))

    def mark_report_undelivered(
        self,
        job_id: str,
        *,
        terminal_reason: str,
        retryable: bool = False,
        status_code: int | None = None,
    ) -> None:
        self.undelivered.append(
            {
                "job_id": job_id,
                "terminal_reason": terminal_reason,
                "retryable": retryable,
                "status_code": status_code,
            }
        )


# --- four-state table (AC-1): each row distinct --------------------------------


def test_outcome_table_2xx_maps_to_done_phase() -> None:
    assert terminal_post_delivered(201) is True
    assert terminal_phase_for_status("done") == PHASE_TERMINAL_DONE


def test_outcome_table_4xx_permanent_reject_not_retryable() -> None:
    assert terminal_post_permanent_reject(413) is True
    assert terminal_post_retryable(413) is False
    assert terminal_reason_for_status(413) == "bus_reject_413"
    assert terminal_phase_for_status(STATUS_REPORT_UNDELIVERED) == (
        PHASE_TERMINAL_REPORT_UNDELIVERED
    )


def test_outcome_table_5xx_transport_is_retryable() -> None:
    assert terminal_post_retryable(503) is True
    assert terminal_post_retryable(599) is True
    assert terminal_reason_for_status(503) == "bus_transport_error"
    assert terminal_phase_for_status("failed") == PHASE_TERMINAL_FAILED
    assert PHASE_TERMINAL_FAILED != PHASE_TERMINAL_REPORT_UNDELIVERED


def test_outcome_table_crash_before_reply_leaves_claimed_not_terminal() -> None:
    """Post never attempted — no ledger terminalization helper runs."""
    queue = AutoJobQueue(durable=False)
    job = queue.enqueue(
        thread_id="6655",
        turn_number=1,
        subject="s",
        body="b",
        from_agent="web-anthropic",
        to_agent="cursor",
        desired_model="auto",
        desired_effort="medium",
        contract="propagate",
    )
    queue.claim_next()
    assert queue.get(job.job_id).status == "claimed"
    assert queue.snapshot()["done"] == 0
    assert queue.snapshot()["report_undelivered"] == 0


# --- AC-2: 413 leaves report_undelivered, not done or failed -------------------


@pytest.mark.asyncio
async def test_413_terminal_post_leaves_report_undelivered_not_done_or_failed() -> None:
    client = AsyncMock()
    client.reply = AsyncMock(return_value=MagicMock(status_code=413, body={}))
    queue = _RecordingQueue()
    result = await post_terminal_status(
        _job(),
        client=client,
        queue=queue,
        summary="propagated",
        disposition="propagated",
        contract="propagate",
        payload={"summary": "propagated", "executions": []},
    )
    assert result["ok"] is False
    assert result["delivered"] is False
    assert result["status_code"] == 413
    assert queue.done == []
    assert len(queue.undelivered) == 1
    assert queue.undelivered[0]["terminal_reason"] == "bus_reject_413"
    assert queue.undelivered[0]["retryable"] is False


@pytest.mark.asyncio
async def test_413_durable_ledger_is_report_undelivered() -> None:
    queue = get_queue()
    job = queue.enqueue(
        thread_id="6655",
        turn_number=1297,
        subject="propagate",
        body="TYPE: DIRECTIVE\n",
        from_agent="cdp-operator",
        to_agent="cursor",
        desired_model="auto",
        desired_effort="medium",
        contract="propagate",
    )
    queue.claim_next()
    client = AsyncMock()
    client.reply = AsyncMock(return_value=MagicMock(status_code=413, body={}))
    await post_terminal_status(
        job,
        client=client,
        queue=queue,
        summary="propagated",
        disposition="propagated",
        contract="propagate",
        payload={"summary": "propagated"},
    )
    view = get_ledger().observer_state(job_id=job.job_id)
    assert view is not None
    assert view["status"] == STATUS_REPORT_UNDELIVERED
    assert view["lifecycle_phase"] == PHASE_TERMINAL_REPORT_UNDELIVERED
    assert view["status"] != "done"
    assert view["status"] != "failed"
    assert view["terminal_reason"] == "bus_reject_413"


@pytest.mark.asyncio
async def test_2xx_marks_done_not_report_undelivered() -> None:
    client = AsyncMock()
    client.reply = AsyncMock(return_value=MagicMock(status_code=201, body={}))
    queue = _RecordingQueue()
    await post_terminal_status(
        _job(),
        client=client,
        queue=queue,
        summary="propagated",
        disposition="propagated",
        contract="propagate",
        payload={"summary": "propagated"},
    )
    assert queue.done == [("job-honesty", False)]
    assert queue.undelivered == []
    assert client.reply.await_args.kwargs["allow_long_body"] is False


@pytest.mark.asyncio
async def test_503_marks_report_undelivered_retryable() -> None:
    client = AsyncMock()
    client.reply = AsyncMock(return_value=MagicMock(status_code=503, body={}))
    queue = _RecordingQueue()
    await post_terminal_status(
        _job(),
        client=client,
        queue=queue,
        summary="propagated",
        disposition="propagated",
        contract="propagate",
        payload={"summary": "propagated"},
    )
    assert queue.done == []
    assert queue.undelivered[0]["retryable"] is True
    assert queue.undelivered[0]["terminal_reason"] == "bus_transport_error"


# --- AC-3: propagate spill + sidecar pointer ---------------------------------


def test_compact_propagate_moves_large_proof_to_spill_map() -> None:
    payload = {
        "summary": "propagated",
        "executions": [
            {
                "service": "cortex_api",
                "row_id": "row-1",
                "status": "executed",
                "proof": _large_proof(),
                "proof_before": {"small": "ok"},
            }
        ],
    }
    compact = compact_propagate_terminal_payload(payload)
    assert "proof_spill" in compact
    assert "row-1.proof" in compact["proof_spill"]
    assert compact["executions"][0]["proof"]["spilled"] is True
    assert compact["executions"][0]["proof_before"] == {"small": "ok"}


def test_propagate_terminal_soft_spill_writes_sidecar_pointer() -> None:
    payload = compact_propagate_terminal_payload(
        {
            "summary": "propagated",
            "executions": [
                {
                    "service": "cortex_api",
                    "row_id": "row-1",
                    "status": "executed",
                    "proof": _large_proof(30_000),
                }
            ],
        }
    )
    body = json.dumps(payload, indent=2)
    prepared = prepare_body_for_insert(
        thread="6655",
        subject="status:done — propagate",
        body=body,
        from_agent="cursor-auto",
        allow_long_body=False,
    )
    assert prepared.sidecar_uri is not None
    assert "Sidecar:" in prepared.body
    assert len(prepared.body) < len(body)


def test_allow_long_body_disabled_on_terminal_post() -> None:
    async def _run() -> None:
        client = AsyncMock()
        client.reply = AsyncMock(return_value=MagicMock(status_code=200, body={}))
        queue = _RecordingQueue()
        await post_terminal_status(
            _job(),
            client=client,
            queue=queue,
            summary="ok",
            disposition="propagated",
            contract="propagate",
            payload={"summary": "ok"},
        )
        assert client.reply.await_args.kwargs["allow_long_body"] is False

    asyncio.run(_run())
