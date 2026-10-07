"""Each journal transition emits one jobs.run event after the commit."""

from __future__ import annotations

import json
import re
import subprocess

import pytest

from jobs import events
from jobs.journal import Journal

_SIGNALS = (
    "jobs.run.admitted",
    "jobs.run.started",
    "jobs.run.progress",
    "jobs.run.cancelling",
    "jobs.run.completed",
    "jobs.run.failed",
    "jobs.run.cancelled",
    "jobs.run.lost",
    "jobs.run.delivered",
    "jobs.run.undelivered",
    "jobs.run.rejected",
)


@pytest.mark.offline
def test_transition_emits_after_commit(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JOBS_STATE_DIR", str(tmp_path))
    events.reset_published_events()
    journal = Journal(tmp_path / "journal.db")
    seen: list[str] = []

    def spy(event: object) -> None:
        signal = getattr(event, "signal")
        if signal != "jobs.run.rejected" and signal != "jobs.run.progress":
            assert journal.fold("run-1") is not None
        seen.append(signal)

    monkeypatch.setattr(events, "publish", spy)
    journal.admit(
        run_id="run-1",
        job="ticker",
        args={},
        surface="code",
        output_contract="inline",
        target_thread=None,
    )
    journal.append("run-1", "running", {"pid": 1, "pgid": 1})
    events.emit_progress(run_id="run-1", progress_seq=1, nbytes=4)
    journal.append("run-1", "cancelling", {})
    journal.append("run-1", "cancelled", {"signal": "SIGTERM"})
    journal.append(
        "run-1",
        "failed",
        {"exit_code": 1, "error": {"code": "exit_nonzero"}},
    )
    journal.append("run-1", "completed", {"exit_code": 0, "duration_ms": 1})
    journal.append("run-1", "lost", {"recovery": "restart_reconcile"})
    journal.append("run-1", "undelivered", {"http_status": 503})
    journal.append("run-1", "delivered", {"turn_number": 3})
    events.emit_rejected(job="ticker", code="args_invalid", surface="code")
    assert seen == [
        "jobs.run.admitted",
        "jobs.run.started",
        "jobs.run.progress",
        "jobs.run.cancelling",
        "jobs.run.cancelled",
        "jobs.run.failed",
        "jobs.run.completed",
        "jobs.run.lost",
        "jobs.run.undelivered",
        "jobs.run.delivered",
        "jobs.run.rejected",
    ]


@pytest.mark.offline
def test_signal_names_and_catalog() -> None:
    pattern = re.compile(r"^[a-z]+(\.[a-z]+){1,4}$")
    assert len(_SIGNALS) == 11
    for name in _SIGNALS:
        assert pattern.fullmatch(name)
    check = subprocess.run(
        ["scripts/gen-event-catalog", "check"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert check.returncode == 0, check.stdout + check.stderr


@pytest.mark.offline
def test_publish_fills_sink_and_file_when_socket_missing(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Missing Event Service socket must not drop the spy or the file sink."""
    sink = tmp_path / "events.ndjson"
    monkeypatch.setenv("JOBS_EVENT_SINK", str(sink))
    monkeypatch.setenv("EVENTS_INGEST_SOCK", str(tmp_path / "absent.sock"))
    events.reset_published_events()
    events.publish(
        events.jobs_run_admitted(
            run_id="run-sink",
            job="ticker",
            surface="code",
            output_contract="inline",
        )
    )
    assert [e.signal for e in events.published_events()] == ["jobs.run.admitted"]
    assert sink.read_text(encoding="utf-8") == "jobs.run.admitted\n"


@pytest.mark.offline
def test_publish_emits_event_service_line(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Event Service emit carries source=jobs and the Event fields."""
    sent: list[bytes] = []

    class _Sock:
        def settimeout(self, _seconds: float) -> None:
            return None

        def connect(self, _path: str) -> None:
            return None

        def sendall(self, data: bytes) -> None:
            sent.append(data)

        def __enter__(self) -> _Sock:
            return self

        def __exit__(self, *_exc: object) -> None:
            return None

    monkeypatch.setenv("JOBS_EVENT_SINK", "")
    monkeypatch.setattr(events.socket, "socket", lambda *_a, **_k: _Sock())
    events.reset_published_events()
    events.publish(events.jobs_run_lost(run_id="run-es", recovery="restart_reconcile"))
    assert len(sent) == 1
    line = json.loads(sent[0].decode())
    assert line["signal"] == "jobs.run.lost"
    assert line["source"] == "jobs"
    assert line["role"] == "observation"
    assert line["scope"] == "node"
    assert line["payload"] == {"run_id": "run-es", "recovery": "restart_reconcile"}
    assert [e.signal for e in events.published_events()] == ["jobs.run.lost"]
