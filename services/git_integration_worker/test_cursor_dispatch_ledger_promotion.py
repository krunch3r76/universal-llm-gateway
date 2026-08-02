"""Regression: queued rows survive worker restart and must promote on the live worker."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from services.git_integration_worker.cursor_dispatch_ledger import CursorDispatchLedger
from services.git_integration_worker.models.cursor_api import (
    CursorDispatchRequest,
    CursorDispatchResponse,
)


@pytest.fixture()
def ledger(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> CursorDispatchLedger:
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    CursorDispatchLedger._instance = None
    return CursorDispatchLedger.instance()


def _insert_queued(
    ledger: CursorDispatchLedger,
    *,
    dispatch_id: str,
    thread_id: str,
    source_repo: str,
    worker_instance: str,
) -> None:
    with ledger._connect() as conn:
        conn.execute(
            "INSERT INTO cursor_sdk_dispatches "
            "(dispatch_id, fingerprint, thread_id, execution_id, resolved_model, "
            " message_present, status, record_json, source_repo, read_only, "
            " worker_instance, queued_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                dispatch_id,
                "fp",
                thread_id,
                "exec-1",
                "cursor/composer-2.5",
                0,
                "queued",
                json.dumps({"model": "cursor/composer-2.5"}),
                source_repo,
                0,
                worker_instance,
                "2026-06-18T21:10:00+00:00",
            ),
        )


def test_promote_next_queued_rehomes_orphan_from_dead_worker(
    ledger: CursorDispatchLedger,
) -> None:
    repo = "/mnt/torus/projects/universal-llm-gateway"
    dead_worker = "4689f00e-149f-4bdf-8be8-425a0b4b4428"
    live_worker = "68b97527-82a0-469e-8e88-7ee1c1f848bb"
    _insert_queued(
        ledger,
        dispatch_id="cb532cacde64-44e410dc",
        thread_id="2671",
        source_repo=repo,
        worker_instance=dead_worker,
    )

    promoted = ledger.promote_next_queued(
        source_repo=repo, worker_instance=live_worker
    )

    assert promoted is not None
    assert promoted.dispatch_id == "cb532cacde64-44e410dc"
    with ledger._connect() as conn:
        row = conn.execute(
            "SELECT status, worker_instance FROM cursor_sdk_dispatches "
            "WHERE dispatch_id=?",
            ("cb532cacde64-44e410dc",),
        ).fetchone()
    assert row["status"] == "admitted"
    assert row["worker_instance"] == live_worker


def test_promote_next_queued_fifo_across_worker_instances(
    ledger: CursorDispatchLedger,
) -> None:
    repo = "/mnt/torus/projects/universal-llm-gateway"
    _insert_queued(
        ledger,
        dispatch_id="first-queued",
        thread_id="1",
        source_repo=repo,
        worker_instance="old-a",
    )
    _insert_queued(
        ledger,
        dispatch_id="second-queued",
        thread_id="2",
        source_repo=repo,
        worker_instance="old-b",
    )

    promoted = ledger.promote_next_queued(source_repo=repo, worker_instance="live")
    assert promoted is not None
    assert promoted.dispatch_id == "first-queued"

    with ledger._connect() as conn:
        conn.execute(
            "UPDATE cursor_sdk_dispatches SET status='failed', "
            "terminal_status='failed', terminal_at='2026-06-18T23:00:00+00:00' "
            "WHERE dispatch_id='first-queued'"
        )

    promoted2 = ledger.promote_next_queued(source_repo=repo, worker_instance="live")
    assert promoted2 is not None
    assert promoted2.dispatch_id == "second-queued"


def test_startup_reconcile_includes_repos_with_orphaned_queued(
    ledger: CursorDispatchLedger,
) -> None:
    repo = "/mnt/torus/projects/universal-llm-gateway"
    _insert_queued(
        ledger,
        dispatch_id="orphan-queued",
        thread_id="99",
        source_repo=repo,
        worker_instance="dead-worker",
    )

    repos = ledger.startup_reconcile(worker_instance="live-worker")

    assert repo in repos


def test_admit_queues_behind_prior_queued_rows(
    ledger: CursorDispatchLedger,
) -> None:
    repo = "/mnt/torus/projects/universal-llm-gateway"
    _insert_queued(
        ledger,
        dispatch_id="ahead",
        thread_id="1",
        source_repo=repo,
        worker_instance="dead-worker",
    )
    req = CursorDispatchRequest(
        thread_id="2",
        model="cursor/composer-2.5",
        dispatch_id="new-dispatch",
        execution_id="exec-new",
        message="test prompt",
    )
    admission = CursorDispatchResponse(
        admitted=True,
        dispatch_id=req.dispatch_id,
        thread_id=req.thread_id,
        model_id="cursor/composer-2.5",
    )

    result = ledger.admit(
        req=req,
        fingerprint=ledger.fingerprint(req),
        execution_id="exec-new",
        caller_agent=None,
        resolved_model="cursor/composer-2.5",
        admission=admission,
        source_repo=repo,
        worker_instance="live-worker",
    )

    assert result is not None
    assert result.status == "queued"
    assert result.queue_position == 2


def test_demote_admitted_to_queued_restores_fifo_head(
    ledger: CursorDispatchLedger,
) -> None:
    repo = "/mnt/torus/projects/universal-llm-gateway"
    holder = CursorDispatchRequest(
        thread_id="1",
        model="cursor/composer-2.5",
        dispatch_id="holder-demote",
        execution_id="exec-holder",
        message="holder",
    )
    successor = CursorDispatchRequest(
        thread_id="2",
        model="cursor/composer-2.5",
        dispatch_id="queued-head",
        execution_id="exec-1",
        message="successor",
    )
    admission = CursorDispatchResponse(
        admitted=True,
        dispatch_id=successor.dispatch_id,
        thread_id=successor.thread_id,
        model_id="cursor/composer-2.5",
    )
    ledger.admit(
        req=holder,
        fingerprint=ledger.fingerprint(holder),
        execution_id=holder.execution_id,
        caller_agent=None,
        resolved_model="cursor/composer-2.5",
        admission=admission,
        source_repo=repo,
        worker_instance="worker-a",
    )
    ledger.mark_running(dispatch_id=holder.dispatch_id)
    ledger.admit(
        req=successor,
        fingerprint=ledger.fingerprint(successor),
        execution_id=successor.execution_id,
        caller_agent=None,
        resolved_model="cursor/composer-2.5",
        admission=admission,
        source_repo=repo,
        worker_instance="worker-a",
    )
    ledger.mark_terminal(dispatch_id=holder.dispatch_id, terminal_status="completed")
    promoted = ledger.promote_next_queued(
        source_repo=repo, worker_instance="worker-b"
    )
    assert promoted is not None
    assert promoted.dispatch_id == "queued-head"

    restored = ledger.demote_admitted_to_queued(dispatch_id="queued-head")
    assert restored is True

    with ledger._connect() as conn:
        row = conn.execute(
            "SELECT status, terminal_status FROM cursor_sdk_dispatches "
            "WHERE dispatch_id=?",
            ("queued-head",),
        ).fetchone()
    assert row["status"] == "queued"
    assert row["terminal_status"] is None

    repromoted = ledger.promote_next_queued(
        source_repo=repo, worker_instance="worker-c"
    )
    assert repromoted is not None
    assert repromoted.dispatch_id == "queued-head"
