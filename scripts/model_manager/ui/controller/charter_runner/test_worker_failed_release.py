"""Unit tests for worker-failed WIP release (sticky CONSULT_ADMITTED hole)."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from libs.charter_runner_store.db import open_ledger_db
from scripts.model_manager.ui.controller.charter_runner import bus_client, worker_failed_release
from scripts.model_manager.ui.controller.charter_runner.root_ledger import (
    RootStatus,
    SeedConfirm,
    Transition,
    load_root,
    seed_from_confirm,
    upsert_root,
)
from scripts.model_manager.ui.controller.charter_runner.worker_failed_release import (
    maybe_release_failed_window_wip,
)


@pytest.fixture
def ledger(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    db = tmp_path / "ledger.sqlite"
    monkeypatch.setattr(
        "scripts.model_manager.ui.controller.charter_runner.root_ledger.default_ledger_path",
        lambda: db,
    )
    monkeypatch.setattr(worker_failed_release, "write_cortex_mirror", lambda _row: "")
    conn = open_ledger_db(db)
    try:
        yield conn
    finally:
        conn.close()


@pytest.mark.offline
def test_failure_reason_detects_gateway_slot_timeout_json() -> None:
    turns = [
        {
            "turn_number": 1,
            "subject": "cursor-sdk generate — e39475f7",
            "body": "packet",
        },
        {
            "turn_number": 2,
            "subject": "cursor-sdk dispatch 4d893c7634e9-fd238327 FAILED (slot acquire timeout)",
            "body": (
                '{"code":"CURSOR_SDK_SLOT_ACQUIRE_TIMEOUT","message":"x",'
                '"source":"gateway","retryable":true}'
            ),
        },
    ]
    assert (
        bus_client.failure_reason_from_worker_turns(turns)
        == "CURSOR_SDK_SLOT_ACQUIRE_TIMEOUT"
    )


@pytest.mark.offline
def test_failure_reason_ignores_complete_closeout() -> None:
    turns = [
        {
            "turn_number": 2,
            "subject": "cursor-sdk dispatch abc",
            "body": '{"schema_version":1,"status":"complete","summary":"ok"}',
        },
    ]
    assert bus_client.failure_reason_from_worker_turns(turns) is None


@pytest.mark.offline
@pytest.mark.asyncio
async def test_release_clears_consult_admitted_wip(ledger, monkeypatch) -> None:
    seed_from_confirm(
        ledger,
        SeedConfirm(
            root_id="6409",
            pickup_gid="G5",
            pickup_lane="judgment",
            attendance="autonomous",
            scoreboard_uri="cortex://notes/system/threads/6409-charter-scoreboard.md",
        ),
    )
    row = load_root(ledger, "6409")
    assert row is not None
    upsert_root(
        ledger,
        replace(
            row,
            status=RootStatus.CONSULT_ADMITTED,
            wip_window_id="charter-6409-w1",
            consult_role="judgment_gap",
            last_transition=Transition.ADMIT_CONSULT.value,
        ),
    )
    ledger.execute(
        """
        INSERT INTO consult_queue
          (root_id, gid, consult_role, corpus_sha, attempts, next_retry, status,
           created_at, updated_at)
        VALUES ('6409', 'G5', 'judgment_gap', NULL, 0, NULL, 'queued', 1, 1)
        """
    )
    ledger.commit()

    async def _fail(_thread: str) -> str:
        return "CURSOR_SDK_SLOT_ACQUIRE_TIMEOUT"

    monkeypatch.setattr(bus_client, "worker_failure_reason", _fail)
    turns = [
        {
            "turn_number": 16,
            "subject": "WIP charter-runner window 1",
            "body": (
                '{"charter_runner":true,"window":1,'
                '"worker_thread":"6421","admission_mode":"consult"}'
            ),
        },
    ]
    live, reason = await maybe_release_failed_window_wip(
        ledger, load_root(ledger, "6409"), turns
    )
    assert reason == "CURSOR_SDK_SLOT_ACQUIRE_TIMEOUT"
    assert live.status == RootStatus.CONSULT_DEFERRED
    assert live.wip_window_id is None
    assert live.last_transition == Transition.WORKER_FAILED.value
    assert live.consult_attempts == 1
    assert live.consult_next_retry is not None
    q = ledger.execute(
        "SELECT attempts, status, next_retry FROM consult_queue "
        "WHERE root_id='6409' AND gid='G5'"
    ).fetchone()
    assert q["status"] == "queued"
    assert int(q["attempts"]) == 1
    assert q["next_retry"] is not None
