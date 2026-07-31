"""Write-lease holder projection on queued admit + lease-snapshot."""

from __future__ import annotations

import json
from datetime import UTC, datetime
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


def _insert_running_holder(
    ledger: CursorDispatchLedger,
    *,
    dispatch_id: str,
    thread_id: str,
    source_repo: str,
    message: str | None = None,
    packet_path: str | None = None,
) -> None:
    record = {"model": "cursor/claude-opus-4-8"}
    if message is not None:
        record["message"] = message
    if packet_path is not None:
        record["packet_path"] = packet_path
    with ledger._connect() as conn:
        conn.execute(
            "INSERT INTO cursor_sdk_dispatches "
            "(dispatch_id, fingerprint, thread_id, execution_id, resolved_model, "
            " packet_path, message_present, status, record_json, source_repo, "
            " read_only, worker_instance, started_at, last_heartbeat_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                dispatch_id,
                "fp-holder",
                thread_id,
                "exec-holder",
                "cursor/claude-opus-4-8",
                packet_path,
                1 if message else 0,
                "running",
                json.dumps(record),
                source_repo,
                0,
                "worker-live",
                datetime.now(UTC).isoformat(),
                datetime.now(UTC).isoformat(),
            ),
        )


def test_queued_admit_includes_holder_when_writer_active(
    ledger: CursorDispatchLedger,
) -> None:
    repo = "/mnt/torus/projects/universal-llm-gateway"
    _insert_running_holder(
        ledger,
        dispatch_id="d75f84f73338",
        thread_id="5272",
        source_repo=repo,
        message="Opus B-leg blend on bus 5267\nmore lines ignored",
    )
    req = CursorDispatchRequest(
        thread_id="5273",
        model="cursor/composer-2.5",
        dispatch_id="f17288aacc1f",
        execution_id="exec-new",
        message="implement packet",
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
        worker_instance="worker-live",
    )

    assert result is not None
    assert result.status == "queued"
    assert result.holder_dispatch_id == "d75f84f73338"
    assert result.holder_thread_id == "5272"
    assert result.holder_resolved_model == "cursor/claude-opus-4-8"
    assert result.holder_subject_preview == "Opus B-leg blend on bus 5267"
    assert result.holder_status == "running"


def test_queued_admit_null_holder_when_only_prior_queued(
    ledger: CursorDispatchLedger,
) -> None:
    repo = "/mnt/torus/projects/universal-llm-gateway"
    with ledger._connect() as conn:
        conn.execute(
            "INSERT INTO cursor_sdk_dispatches "
            "(dispatch_id, fingerprint, thread_id, execution_id, resolved_model, "
            " message_present, status, record_json, source_repo, read_only, "
            " worker_instance, queued_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "ahead-queued",
                "fp",
                "1",
                "exec-1",
                "cursor/composer-2.5",
                0,
                "queued",
                json.dumps({"model": "cursor/composer-2.5"}),
                repo,
                0,
                "dead-worker",
                datetime.now(UTC).isoformat(),
            ),
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
    assert result.holder_dispatch_id is None
    assert result.holder_thread_id is None


def test_lease_snapshot_enriched_fields(
    ledger: CursorDispatchLedger,
) -> None:
    repo = "/mnt/torus/projects/universal-llm-gateway"
    _insert_running_holder(
        ledger,
        dispatch_id="holder-repo",
        thread_id="5272",
        source_repo=repo,
        packet_path="tmp/prompts/implement-packet.md",
    )

    snap = ledger.lease_snapshot(source_repo=repo)

    assert snap["holder_dispatch_id"] == "holder-repo"
    assert snap["holder_thread_id"] == "5272"
    assert snap["holder_resolved_model"] == "cursor/claude-opus-4-8"
    assert snap["holder_subject_preview"] == "implement-packet.md"
    assert snap["holder_status"] == "running"
    assert snap["holder_started_at"] is not None
    assert snap["holder_last_heartbeat_at"] is not None
    assert snap["holder_source_repo"] == repo


def test_subject_preview_truncates_long_first_line(
    ledger: CursorDispatchLedger,
) -> None:
    repo = "/mnt/torus/projects/universal-llm-gateway"
    long_line = "x" * 200
    _insert_running_holder(
        ledger,
        dispatch_id="long-subject",
        thread_id="99",
        source_repo=repo,
        message=long_line,
    )

    snap = ledger.lease_snapshot(source_repo=repo)

    assert snap["holder_subject_preview"] is not None
    assert len(snap["holder_subject_preview"]) == 120
