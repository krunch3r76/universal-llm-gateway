"""Bridge stderr capture and unowned-bridge sweep (assertion 31706).

The dispatch path never saw why a bridge died — only the ``Connection refused``
that followed. These cover the two halves of the fix: draining the pipe so the
exit reason survives, and collecting bridges no dispatch owns any more.
"""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import pytest

from services.git_integration_worker import cursor_sdk_bridge_stderr as tap_mod
from services.git_integration_worker import cursor_sdk_orphan as orphan_mod
from services.git_integration_worker.cursor_sdk_bridge_stderr import (
    bridge_exit_snapshot,
    start_bridge_stderr_drain,
    stop_bridge_stderr_drain,
)
from services.git_integration_worker.cursor_sdk_orphan import sweep_unowned_bridges


class _FakeBridge:
    def __init__(self, process: subprocess.Popen[str]) -> None:
        self.process = process


class _FakeClient:
    """Stands in for ``cursor_sdk.Client`` with a launched bridge."""

    def __init__(self, process: subprocess.Popen[str] | None) -> None:
        self._owned_bridge = _FakeBridge(process) if process is not None else None


def _spawn(code: str) -> subprocess.Popen[str]:
    return subprocess.Popen(  # noqa: S603
        [sys.executable, "-c", code],
        stderr=subprocess.PIPE,
        stdout=subprocess.DEVNULL,
        text=True,
    )


@pytest.fixture(autouse=True)
def _stderr_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tap_mod, "_STDERR_DIR", tmp_path / "bridge-stderr")


@pytest.fixture(autouse=True)
def _captured_exits(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """Intercept the exit signal so tests never publish to the event bus."""
    emitted: list[dict[str, Any]] = []
    monkeypatch.setattr(
        tap_mod,
        "emit_sdk_bridge_exited",
        lambda **kwargs: emitted.append(kwargs),
    )
    return emitted


def _await_drain(tap: tap_mod.BridgeStderrTap, timeout: float = 10.0) -> None:
    assert tap.thread is not None
    tap.thread.join(timeout=timeout)
    assert not tap.thread.is_alive(), "drain thread did not finish"


def test_drain_captures_stderr_and_exit_code(
    _captured_exits: list[dict[str, Any]],
) -> None:
    proc = _spawn(
        "import sys; sys.stderr.write('boom line 1\\nboom line 2\\n'); "
        "sys.stderr.flush(); sys.exit(3)"
    )
    tap = start_bridge_stderr_drain(
        dispatch_id="d-exit-code", thread_id="t-1", client=_FakeClient(proc)
    )
    assert tap is not None
    _await_drain(tap)

    assert tap.tail() == ["boom line 1", "boom line 2"]
    assert tap.log_path.read_text(encoding="utf-8") == "boom line 1\nboom line 2\n"

    snapshot = bridge_exit_snapshot(tap)
    assert snapshot["bridge_exit_code"] == 3
    assert snapshot["bridge_signal"] is None
    assert snapshot["bridge_alive"] is False
    assert snapshot["bridge_stderr_tail"] == ["boom line 1", "boom line 2"]
    assert snapshot["bridge_stderr_log"] == str(tap.log_path)

    assert len(_captured_exits) == 1
    assert _captured_exits[0]["exit_code"] == 3
    assert _captured_exits[0]["dispatch_id"] == "d-exit-code"


def test_drain_decodes_kill_signal(_captured_exits: list[dict[str, Any]]) -> None:
    proc = _spawn(
        "import sys, time; sys.stderr.write('alive\\n'); sys.stderr.flush(); time.sleep(60)"
    )
    tap = start_bridge_stderr_drain(
        dispatch_id="d-signal", thread_id="t-2", client=_FakeClient(proc)
    )
    assert tap is not None
    # Wait for the first line so the drain is definitely reading before the kill.
    deadline = time.monotonic() + 10.0
    while not tap.tail() and time.monotonic() < deadline:
        time.sleep(0.05)
    proc.kill()
    _await_drain(tap)

    snapshot = bridge_exit_snapshot(tap)
    assert snapshot["bridge_signal"] == "SIGKILL"
    assert snapshot["bridge_exit_code"] is None
    assert _captured_exits[0]["signal_name"] == "SIGKILL"


def test_drain_head_cap_retains_tail(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tap_mod, "_MAX_HEAD_BYTES", 64)
    proc = _spawn(
        "import sys\n"
        "for i in range(200): sys.stderr.write('line-%03d\\n' % i)\n"
        "sys.stderr.flush()\n"
    )
    tap = start_bridge_stderr_drain(
        dispatch_id="d-cap", thread_id="t-3", client=_FakeClient(proc)
    )
    assert tap is not None
    _await_drain(tap)

    body = tap.log_path.read_text(encoding="utf-8")
    assert "head cap 64 bytes reached" in body
    assert "--- retained tail ---" in body
    # Head kept, middle dropped from disk, tail preserved.
    assert "line-000" in body
    assert "line-100" not in body
    assert "line-199" in body
    assert tap.byte_count() == 200 * len("line-000\n")
    assert tap.tail()[-1] == "line-199"
    assert len(tap.tail()) == 40


def test_start_returns_none_without_owned_bridge() -> None:
    assert (
        start_bridge_stderr_drain(
            dispatch_id="d-none", thread_id="t-4", client=_FakeClient(None)
        )
        is None
    )


def test_snapshot_of_untapped_dispatch_is_empty() -> None:
    assert bridge_exit_snapshot(None) == {}


def test_planned_teardown_emits_no_exit_signal(
    _captured_exits: list[dict[str, Any]],
) -> None:
    """A dispatch closing its own client is not a bridge death."""
    proc = _spawn("import sys, time; time.sleep(60)")
    tap = start_bridge_stderr_drain(
        dispatch_id="d-planned", thread_id="t-5", client=_FakeClient(proc)
    )
    assert tap is not None
    stop_bridge_stderr_drain(tap)
    proc.kill()
    _await_drain(tap)
    assert _captured_exits == []


def test_abort_forensics_snapshot_preserves_sdk_network_reason() -> None:
    """Merging the snapshot must not disturb the failure classification."""
    from cursor_sdk.errors import NetworkError

    from services.git_integration_worker.cursor_sdk_closeout import (
        degraded_reasons_from_exception,
    )
    from services.git_integration_worker.routes.cursor_sdk import SdkRunAbortedError

    proc = _spawn("import sys; sys.stderr.write('dying\\n'); sys.exit(1)")
    tap = start_bridge_stderr_drain(
        dispatch_id="d-forensics", thread_id="t-6", client=_FakeClient(proc)
    )
    assert tap is not None
    _await_drain(tap)

    forensics = {
        "cause": "ConnectError: [Errno 111] Connection refused",
        "elapsed_s": 12.3,
        **bridge_exit_snapshot(tap),
    }
    assert forensics["bridge_exit_code"] == 1
    assert forensics["bridge_stderr_tail"] == ["dying"]
    assert forensics["cause"].startswith("ConnectError")

    wrapped = SdkRunAbortedError("abort", forensics=forensics)
    wrapped.__cause__ = NetworkError("connection refused")
    assert degraded_reasons_from_exception(wrapped) == ("sdk_network",)


class _FakeProc:
    """psutil.Process stand-in for the sweeper."""

    def __init__(
        self,
        pid: int,
        *,
        age_s: float,
        dispatch_id: str | None = None,
        is_bridge: bool = True,
    ) -> None:
        self.pid = pid
        self.is_bridge = is_bridge
        self.killed = False
        self._create_time = time.time() - age_s
        self._env = (
            {} if dispatch_id is None else {"CURSOR_SDK_DISPATCH_ID": dispatch_id}
        )

    def create_time(self) -> float:
        return self._create_time

    def environ(self) -> dict[str, str]:
        return self._env

    def kill(self) -> None:
        self.killed = True

    def wait(self, timeout: float | None = None) -> int:
        del timeout
        return 0


@pytest.fixture
def _sweep_env(monkeypatch: pytest.MonkeyPatch):
    """Point the sweeper at fake processes and a controllable ledger."""

    def _install(procs: list[_FakeProc], statuses: dict[str, str]) -> None:
        monkeypatch.setattr(
            orphan_mod.psutil, "process_iter", lambda attrs=None: list(procs)
        )
        monkeypatch.setattr(
            orphan_mod, "is_cursor_sdk_bridge_process", lambda proc: proc.is_bridge
        )
        monkeypatch.setattr(
            orphan_mod,
            "_default_status_lookup",
            lambda dispatch_id: (
                {"status": statuses[dispatch_id]} if dispatch_id in statuses else None
            ),
        )

    return _install


def test_sweep_kills_aged_bridge_with_terminal_row(_sweep_env) -> None:
    proc = _FakeProc(101, age_s=86_400, dispatch_id="d-old")
    _sweep_env([proc], {"d-old": "completed"})
    result = sweep_unowned_bridges(min_age_s=1800)
    assert result.killed == [101]
    assert proc.killed is True


def test_sweep_kills_aged_bridge_with_no_ledger_row(_sweep_env) -> None:
    """The pytest-orphan shape: a bridge that never had a dispatch row."""
    proc = _FakeProc(102, age_s=86_400, dispatch_id=None)
    _sweep_env([proc], {})
    assert sweep_unowned_bridges(min_age_s=1800).killed == [102]
    assert proc.killed is True


def test_sweep_spares_bridge_with_running_row(_sweep_env) -> None:
    proc = _FakeProc(103, age_s=86_400, dispatch_id="d-live")
    _sweep_env([proc], {"d-live": "running"})
    assert sweep_unowned_bridges(min_age_s=1800).killed == []
    assert proc.killed is False


def test_sweep_spares_bridge_inside_grace_window(_sweep_env) -> None:
    """Covers the pre-arm handshake: live bridge, row not yet running."""
    proc = _FakeProc(104, age_s=5, dispatch_id=None)
    _sweep_env([proc], {})
    assert sweep_unowned_bridges(min_age_s=1800).killed == []
    assert proc.killed is False


def test_sweep_spares_registered_active_client(_sweep_env) -> None:
    proc = _FakeProc(105, age_s=86_400, dispatch_id="d-active")
    _sweep_env([proc], {"d-active": "completed"})
    orphan_mod.register_active_client(dispatch_id="d-active", client=object())
    try:
        assert sweep_unowned_bridges(min_age_s=1800).killed == []
        assert proc.killed is False
    finally:
        orphan_mod.clear_dispatch_orphan_state(dispatch_id="d-active")


def test_sweep_ignores_non_bridge_process(_sweep_env) -> None:
    proc = _FakeProc(106, age_s=86_400, dispatch_id=None, is_bridge=False)
    _sweep_env([proc], {})
    result = sweep_unowned_bridges(min_age_s=1800)
    assert result.scanned == 0
    assert proc.killed is False


def test_sweep_spares_bridge_when_ledger_unreadable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An unreadable ledger must never be read as 'nobody owns this'."""
    from sqlite3 import OperationalError

    proc = _FakeProc(107, age_s=86_400, dispatch_id="d-unknown")
    monkeypatch.setattr(orphan_mod.psutil, "process_iter", lambda attrs=None: [proc])
    monkeypatch.setattr(
        orphan_mod, "is_cursor_sdk_bridge_process", lambda p: p.is_bridge
    )

    def _boom(dispatch_id: str) -> dict[str, Any] | None:
        raise OperationalError("database is locked")

    monkeypatch.setattr(orphan_mod, "_default_status_lookup", _boom)
    assert sweep_unowned_bridges(min_age_s=1800).killed == []
    assert proc.killed is False
