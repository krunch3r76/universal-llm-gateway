"""Unit tests for CursorDispatchLedger.overlapping_write_dispatches."""

from __future__ import annotations

import pytest

from services.git_integration_worker.cursor_dispatch_ledger import CursorDispatchLedger
from services.git_integration_worker.models.cursor_api import (
    CursorDispatchRequest,
    CursorDispatchResponse,
)

pytestmark = pytest.mark.offline


@pytest.fixture(autouse=True)
def _isolated_ledger(tmp_path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    CursorDispatchLedger._instance = None
    yield tmp_path
    CursorDispatchLedger._instance = None


def _req(**overrides: object) -> CursorDispatchRequest:
    base = {
        "thread_id": "t-overlap",
        "model": "cursor/composer-2.5",
        "dispatch_id": "disp-overlap",
        "execution_id": "exec-disp-overlap",
        "message": "hello",
    }
    base.update(overrides)
    return CursorDispatchRequest(**base)


def _admit_and_terminal(
    ledger: CursorDispatchLedger,
    *,
    dispatch_id: str,
    read_only: bool,
    started_at: str,
    terminal_at: str | None,
    source_repo: str,
    lease_key: str | None = None,
) -> None:
    req = _req(
        dispatch_id=dispatch_id,
        execution_id=f"exec-{dispatch_id}",
        read_only=read_only,
    )
    effective_lease = lease_key or source_repo
    ledger.admit(
        req=req,
        fingerprint=ledger.fingerprint(req),
        execution_id=req.execution_id,
        caller_agent=None,
        resolved_model="composer-2.5",
        admission=CursorDispatchResponse(
            admitted=True,
            dispatch_id=dispatch_id,
            thread_id=req.thread_id,
            model_id="composer-2.5",
        ),
        contract="implement",
        source_repo=source_repo,
        lease_key=effective_lease,
        read_only=read_only,
    )
    ledger.mark_running(dispatch_id=dispatch_id)
    if terminal_at is not None:
        ledger.mark_terminal(dispatch_id=dispatch_id, terminal_status="completed")
    with ledger._connect() as conn:
        conn.execute(
            "UPDATE cursor_sdk_dispatches SET started_at=?, terminal_at=? WHERE dispatch_id=?",
            (started_at, terminal_at, dispatch_id),
        )
        conn.commit()


def test_overlapping_write_dispatches_peer_fully_overlapping(tmp_path) -> None:
    source_repo = str(tmp_path / "repo")
    ledger = CursorDispatchLedger.instance()
    admit_at = "2026-08-16T10:00:00+00:00"
    closeout_at = "2026-08-16T10:30:00+00:00"
    _admit_and_terminal(
        ledger,
        dispatch_id="self-disp",
        read_only=False,
        started_at="2026-08-16T10:05:00+00:00",
        terminal_at="2026-08-16T10:25:00+00:00",
        source_repo=source_repo,
    )
    _admit_and_terminal(
        ledger,
        dispatch_id="peer-overlap",
        read_only=False,
        started_at="2026-08-16T10:10:00+00:00",
        terminal_at="2026-08-16T10:20:00+00:00",
        source_repo=source_repo,
    )
    rows = ledger.overlapping_write_dispatches(
        source_repo=source_repo,
        admit_at=admit_at,
        closeout_at=closeout_at,
        exclude_dispatch_id="self-disp",
    )
    assert {row["dispatch_id"] for row in rows} == {"peer-overlap"}


def test_overlapping_write_dispatches_peer_finished_before_window(tmp_path) -> None:
    source_repo = str(tmp_path / "repo")
    ledger = CursorDispatchLedger.instance()
    _admit_and_terminal(
        ledger,
        dispatch_id="self-disp",
        read_only=False,
        started_at="2026-08-16T10:00:00+00:00",
        terminal_at="2026-08-16T10:30:00+00:00",
        source_repo=source_repo,
    )
    _admit_and_terminal(
        ledger,
        dispatch_id="peer-early",
        read_only=False,
        started_at="2026-08-16T09:00:00+00:00",
        terminal_at="2026-08-16T09:30:00+00:00",
        source_repo=source_repo,
    )
    rows = ledger.overlapping_write_dispatches(
        source_repo=source_repo,
        admit_at="2026-08-16T10:00:00+00:00",
        closeout_at="2026-08-16T10:30:00+00:00",
        exclude_dispatch_id="self-disp",
    )
    assert rows == []


def test_overlapping_write_dispatches_peer_still_running(tmp_path) -> None:
    source_repo = str(tmp_path / "repo")
    ledger = CursorDispatchLedger.instance()
    _admit_and_terminal(
        ledger,
        dispatch_id="self-disp",
        read_only=False,
        started_at="2026-08-16T10:00:00+00:00",
        terminal_at="2026-08-16T10:30:00+00:00",
        source_repo=source_repo,
    )
    _admit_and_terminal(
        ledger,
        dispatch_id="peer-running",
        read_only=False,
        started_at="2026-08-16T10:05:00+00:00",
        terminal_at=None,
        source_repo=source_repo,
    )
    rows = ledger.overlapping_write_dispatches(
        source_repo=source_repo,
        admit_at="2026-08-16T10:00:00+00:00",
        closeout_at="2026-08-16T10:30:00+00:00",
        exclude_dispatch_id="self-disp",
    )
    assert {row["dispatch_id"] for row in rows} == {"peer-running"}
    assert rows[0]["terminal_at"] is None


def test_overlapping_write_dispatches_different_lease_key(tmp_path) -> None:
    source_repo = str(tmp_path / "repo-a")
    other_repo = str(tmp_path / "repo-b")
    ledger = CursorDispatchLedger.instance()
    _admit_and_terminal(
        ledger,
        dispatch_id="self-disp",
        read_only=False,
        started_at="2026-08-16T10:00:00+00:00",
        terminal_at="2026-08-16T10:30:00+00:00",
        source_repo=source_repo,
    )
    _admit_and_terminal(
        ledger,
        dispatch_id="peer-other-lease",
        read_only=False,
        started_at="2026-08-16T10:05:00+00:00",
        terminal_at="2026-08-16T10:20:00+00:00",
        source_repo=other_repo,
    )
    rows = ledger.overlapping_write_dispatches(
        source_repo=source_repo,
        admit_at="2026-08-16T10:00:00+00:00",
        closeout_at="2026-08-16T10:30:00+00:00",
        exclude_dispatch_id="self-disp",
    )
    assert rows == []


def test_overlapping_write_dispatches_exclude_self(tmp_path) -> None:
    source_repo = str(tmp_path / "repo")
    ledger = CursorDispatchLedger.instance()
    _admit_and_terminal(
        ledger,
        dispatch_id="only-self",
        read_only=False,
        started_at="2026-08-16T10:00:00+00:00",
        terminal_at="2026-08-16T10:30:00+00:00",
        source_repo=source_repo,
    )
    rows = ledger.overlapping_write_dispatches(
        source_repo=source_repo,
        admit_at="2026-08-16T10:00:00+00:00",
        closeout_at="2026-08-16T10:30:00+00:00",
        exclude_dispatch_id="only-self",
    )
    assert rows == []
