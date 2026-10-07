"""kill -9 mid-run. Restart reconcile reports lost and clears the child group."""

from __future__ import annotations

import os
import signal
import subprocess
import time
from pathlib import Path

import httpx
import pytest


def _wait_sock(path: Path, proc: subprocess.Popen[bytes], timeout: float = 10) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if path.exists():
            return
        if proc.poll() is not None:
            raise AssertionError(f"fixture app exited {proc.returncode}")
        time.sleep(0.05)
    raise AssertionError("socket did not appear")


@pytest.mark.offline
def test_kill9_midrun_reports_lost(tmp_path: Path) -> None:
    sock = tmp_path / "jobs.sock"
    sink = tmp_path / "events.ndjson"
    env = os.environ.copy()
    env.update(
        {
            "PYTHONPATH": "libs",
            "JOBS_TOKEN": "test-token",
            "JOBS_STATE_DIR": str(tmp_path),
            "JOBS_SOCK": str(sock),
            "JOBS_EVENT_SINK": str(sink),
        }
    )
    python = os.environ.get("UV_PYTHON") or str(Path.home() / ".venvs/universal/bin/python")
    proc = subprocess.Popen(
        [python, "-m", "jobs.tests.fixture_app"],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )
    headers = {"Authorization": "Bearer test-token", "X-ULG-Surface": "code"}
    try:
        _wait_sock(sock, proc)
        transport = httpx.HTTPTransport(uds=str(sock))
        with httpx.Client(transport=transport, base_url="http://jobs", timeout=5) as client:
            created = client.post("/api/v1/jobs/ticker", headers=headers, json={"args": {}})
            assert created.status_code == 202, created.text
            run_id = created.json()["run_id"]
            pgid = None
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                status = client.get(f"/api/v1/jobs/ticker/runs/{run_id}", headers=headers)
                body = status.json()
                if body.get("status") == "running" and body.get("pgid"):
                    pgid = int(body["pgid"])
                    break
                time.sleep(0.05)
            assert pgid is not None
        proc.send_signal(signal.SIGKILL)
        proc.wait(timeout=5)
        sock.unlink(missing_ok=True)
        sink.write_text("", encoding="utf-8")
        proc2 = subprocess.Popen(
            [python, "-m", "jobs.tests.fixture_app"],
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )
        try:
            _wait_sock(sock, proc2)
            transport = httpx.HTTPTransport(uds=str(sock))
            with httpx.Client(transport=transport, base_url="http://jobs", timeout=5) as client:
                status = client.get(f"/api/v1/jobs/ticker/runs/{run_id}", headers=headers)
            body = status.json()
            assert body["status"] == "lost"
            assert body["source"] == "jobs.journal"
            assert body["recovery"] == "restart_reconcile"
            text = sink.read_text(encoding="utf-8")
            assert "jobs.run.lost" in text
            with pytest.raises(ProcessLookupError):
                os.killpg(pgid, 0)
        finally:
            proc2.send_signal(signal.SIGTERM)
            proc2.wait(timeout=5)
    finally:
        if proc.poll() is None:
            proc.send_signal(signal.SIGKILL)
            proc.wait(timeout=5)
