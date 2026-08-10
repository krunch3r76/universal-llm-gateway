"""SIGUSR1 stack-dump arming: file creation and live signal delivery."""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

from services.git_integration_worker import fault_dump

REPO_ROOT = Path(__file__).resolve().parents[3]


def test_arm_creates_dump_file_and_is_idempotent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(fault_dump, "_DUMP_DIR", tmp_path / "logs")
    monkeypatch.setattr(fault_dump, "_dump_handle", None)
    try:
        first = fault_dump.arm_stack_dumps()
        assert first == tmp_path / "logs" / "stackdump.log"
        assert first.exists()
        assert fault_dump.arm_stack_dumps() == first
    finally:
        handle = fault_dump._dump_handle
        if handle is not None:
            import faulthandler

            faulthandler.unregister(signal.SIGUSR1)
            handle.close()
        monkeypatch.setattr(fault_dump, "_dump_handle", None)


def test_sigusr1_dumps_stacks_without_killing_process(tmp_path: Path) -> None:
    """A live child arms, receives SIGUSR1, and survives with stacks written."""
    dump_dir = tmp_path / "logs"
    script = (
        "from services.git_integration_worker.fault_dump import arm_stack_dumps\n"
        "import sys, time\n"
        "arm_stack_dumps()\n"
        "sys.stdout.write('armed\\n')\n"
        "sys.stdout.flush()\n"
        "time.sleep(30)\n"
    )
    env = {
        **os.environ,
        "GIT_WORKER_STACKDUMP_DIR": str(dump_dir),
        "PYTHONPATH": f"{REPO_ROOT}:{REPO_ROOT / 'libs'}",
    }
    proc = subprocess.Popen(
        [sys.executable, "-c", script],
        cwd=REPO_ROOT,
        env=env,
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        assert proc.stdout is not None
        assert proc.stdout.readline().strip() == "armed"
        proc.send_signal(signal.SIGUSR1)
        dump_file = dump_dir / "stackdump.log"
        deadline = time.monotonic() + 5.0
        contents = ""
        while time.monotonic() < deadline:
            if dump_file.exists():
                contents = dump_file.read_text()
                if "Current thread" in contents or "Thread" in contents:
                    break
            time.sleep(0.05)
        assert "Current thread" in contents, contents
        assert "in <module>" in contents, contents
        assert proc.poll() is None, "process died on SIGUSR1"
    finally:
        proc.kill()
        proc.wait(timeout=5)
