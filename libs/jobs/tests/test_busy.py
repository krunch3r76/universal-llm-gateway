"""A live bus-reply-watch arm defers jobs sync_restart."""

from __future__ import annotations

import asyncio

import pytest

from jobs.journal import Journal


@pytest.mark.offline
def test_bus_reply_watch_defers_restart(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JOBS_STATE_DIR", str(tmp_path))
    journal = Journal(tmp_path / "journal.db")
    journal.admit(
        run_id="run-watch",
        job="bus-reply-watch",
        args={"thread": "12"},
        surface="code",
        output_contract="thread",
        target_thread="12",
    )
    journal.append("run-watch", "running", {"pid": 1, "pgid": 1})
    from scripts.model_manager.ui.controller.restart_drain import RestartDrainGate

    outcome = asyncio.run(RestartDrainGate().evaluate("jobs", force=False))
    assert outcome is not None
    assert outcome.state == "busy"
    assert outcome.service == "jobs"
