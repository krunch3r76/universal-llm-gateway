"""Tests for confined propagation ready-join (arc 6655 Rank 2/3)."""

from __future__ import annotations

import threading
import time
from unittest.mock import patch

import pytest

from scripts.model_manager.ui.controller.propagation_ready_join import (
    DEFER_READY_TIMEOUT,
    DEFER_READY_WAIT,
    ready_join_for_settle,
)


def test_ready_join_skipped_for_non_cdp_services() -> None:
    result = ready_join_for_settle("mcp", ready_timeout_s=0.01)
    assert result.outcome == "skipped"
    assert result.payload is None


def test_ready_join_returns_when_probe_ready() -> None:
    payload = {"status": "ok", "code_version": "sha", "pid": 42}
    with patch(
        "services.git_integration_worker.cursor_auto.propagation_probe.probe_process_live",
        return_value=payload,
    ):
        result = ready_join_for_settle("cdp_ask", ready_timeout_s=1.0)
    assert result.outcome == "ready"
    assert result.payload == payload


def test_ready_join_timeout_defers_with_distinct_token(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("CHARTER_RUNNER_DATA_DIR", str(tmp_path))
    with patch(
        "services.git_integration_worker.cursor_auto.propagation_probe.probe_process_live",
        return_value=None,
    ):
        result = ready_join_for_settle(
            "cdp_ask",
            ready_timeout_s=0.05,
            poll_interval_s=0.01,
        )
    assert result.outcome == "timeout"
    assert result.defer_reason == DEFER_READY_TIMEOUT
    assert result.defer_reason != DEFER_READY_WAIT


def test_ready_join_marks_ready_wait_while_polling(tmp_path, monkeypatch) -> None:
    from charter_runner_store.db import open_ledger_db
    from charter_runner_store.propagation_ledger import list_open_rows

    monkeypatch.setenv("CHARTER_RUNNER_DATA_DIR", str(tmp_path))
    sha = "abc1230000000000000000000000000000000000"
    db = open_ledger_db()
    try:
        db.execute(
            """
            INSERT INTO propagation_ledger (
              row_id, service, action, code_ref, safe_window, proof, proof_class,
              status, age_in_harvests, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, 'open', 0, 1.0, 1.0)
            """,
            (
                f"cdp_ask:{sha}:sync_restart",
                "cdp_ask",
                "sync_restart",
                sha,
                "standalone_ok",
                "probe",
                "process_live",
            ),
        )
        db.commit()
    finally:
        db.close()
    calls = {"n": 0}

    def _probe(_service: str):
        calls["n"] += 1
        if calls["n"] < 3:
            return None
        return {"status": "ok", "code_version": sha, "pid": 1}

    with patch(
        "services.git_integration_worker.cursor_auto.propagation_probe.probe_process_live",
        side_effect=_probe,
    ):
        result = ready_join_for_settle(
            "cdp_ask",
            ready_timeout_s=1.0,
            poll_interval_s=0.01,
        )
    assert result.outcome == "ready"
    row = list_open_rows()[0]
    assert row.defer_reason == DEFER_READY_WAIT


def test_ready_join_does_not_spawn_background_thread() -> None:
    before = {t.ident for t in threading.enumerate()}
    with patch(
        "services.git_integration_worker.cursor_auto.propagation_probe.probe_process_live",
        return_value={"status": "ok"},
    ):
        ready_join_for_settle("cdp_ask", ready_timeout_s=0.01)
    after = {t.ident for t in threading.enumerate()}
    assert after == before
