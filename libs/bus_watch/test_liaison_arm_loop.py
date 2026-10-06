"""Arm-script safety for a:38446 (B2/B3 + detach)."""

from __future__ import annotations

import json
import os
import signal
import subprocess
import time
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
_ARM = _REPO / "scripts" / "liaison-arm-loop.sh"
_PY = Path.home() / ".venvs" / "universal" / "bin" / "python"


def _run_arm(
    watch_dir: Path,
    *,
    root: str,
    holder: str,
    take_over: bool = False,
) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "WATCH_DIR": str(watch_dir)}
    cmd = [
        str(_ARM),
        "--root",
        root,
        "--register",
        "attended",
        "--holder",
        holder,
        "--poll",
        "60",
        "--heartbeat",
        "1200",
    ]
    if take_over:
        cmd.append("--take-over")
    return subprocess.run(
        cmd,
        cwd=_REPO,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )


def _last_json(stdout: str) -> dict:
    for line in reversed(stdout.splitlines()):
        line = line.strip()
        if line.startswith("{"):
            return json.loads(line)
    raise AssertionError(f"no JSON in stdout: {stdout!r}")


def _release(root: str, holder: str) -> None:
    subprocess.run(
        [
            str(_PY),
            str(_REPO / "scripts" / "liaison-tick.py"),
            "--root",
            root,
            "--release",
            "--holder",
            holder,
        ],
        cwd=_REPO,
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )


@pytest.fixture()
def watch_dir(tmp_path: Path) -> Path:
    d = tmp_path / "watchers"
    d.mkdir()
    return d


def test_arm_detaches_and_writes_meta(watch_dir: Path) -> None:
    root = "38446-arm-a"
    holder = "ide:a38446-arm-a"
    try:
        proc = _run_arm(watch_dir, root=root, holder=holder)
        assert proc.returncode == 0, proc.stderr
        row = _last_json(proc.stdout)
        assert row["ok"] is True
        assert row["loop"] == "armed"
        pid = int(row["pid"])
        assert (watch_dir / f"liaison-loop-{root}.meta.json").is_file()
        assert (watch_dir / f"liaison-loop-{root}.monitor-heartbeat").is_file()
        # Detached from the arm shell (setsid → reparent).
        status = Path(f"/proc/{pid}/status").read_text(encoding="utf-8")
        ppid = int(
            next(line for line in status.splitlines() if line.startswith("PPid:")).split()[1]
        )
        assert ppid == 1
        os.kill(pid, signal.SIGTERM)
        deadline = time.time() + 5
        while time.time() < deadline and Path(f"/proc/{pid}").exists():
            time.sleep(0.05)
        assert not Path(f"/proc/{pid}").exists()
    finally:
        _release(root, holder)
        subprocess.run(
            ["pkill", "-f", f"liaison-tick[.]py --root {root} --loop"],
            check=False,
        )


def test_foreign_holder_rearm_does_not_kill_live(watch_dir: Path) -> None:
    """B2: second tab without the word must not SIGTERM the first loop."""
    root = "38446-arm-b"
    holder_a = "ide:a38446-arm-b-a"
    holder_b = "ide:a38446-arm-b-b"
    pid_a: int | None = None
    try:
        a = _run_arm(watch_dir, root=root, holder=holder_a)
        assert a.returncode == 0, a.stderr
        pid_a = int(_last_json(a.stdout)["pid"])
        b = _run_arm(watch_dir, root=root, holder=holder_b)
        assert b.returncode == 3, b.stdout + b.stderr
        row = _last_json(b.stdout)
        assert row["ok"] is False
        assert row["loop"] == "refused"
        assert row.get("reason") in {"held", "fable_lock_held"}
        assert Path(f"/proc/{pid_a}").exists()
        meta = json.loads(
            (watch_dir / f"liaison-loop-{root}.meta.json").read_text(encoding="utf-8")
        )
        assert meta["holder"] == holder_a
        assert int(meta["pid"]) == pid_a
    finally:
        if pid_a is not None and Path(f"/proc/{pid_a}").exists():
            os.kill(pid_a, signal.SIGTERM)
        _release(root, holder_a)
        subprocess.run(
            ["pkill", "-f", f"liaison-tick[.]py --root {root} --loop"],
            check=False,
        )


def test_stale_pidfile_does_not_kill_unrelated(watch_dir: Path) -> None:
    """B3: pidfile pointing at sleep must not be SIGTERM'd."""
    root = "38446-arm-c"
    holder = "ide:a38446-arm-c"
    sleeper = subprocess.Popen(["sleep", "300"])
    try:
        (watch_dir / f"liaison-loop-{root}.pid").write_text(
            str(sleeper.pid), encoding="utf-8"
        )
        (watch_dir / f"liaison-loop-{root}.meta.json").write_text(
            json.dumps({"pid": sleeper.pid, "holder": holder, "root": root}),
            encoding="utf-8",
        )
        proc = _run_arm(watch_dir, root=root, holder=holder)
        assert proc.returncode == 0, proc.stderr
        assert sleeper.poll() is None
        row = _last_json(proc.stdout)
        loop_pid = int(row["pid"])
        if Path(f"/proc/{loop_pid}").exists():
            os.kill(loop_pid, signal.SIGTERM)
        assert sleeper.poll() is None
    finally:
        if sleeper.poll() is None:
            sleeper.kill()
            sleeper.wait(timeout=5)
        _release(root, holder)
        subprocess.run(
            ["pkill", "-f", f"liaison-tick[.]py --root {root} --loop"],
            check=False,
        )


def test_monitor_heartbeat_stale_helper(tmp_path: Path) -> None:
    from bus_watch.liaison_loop_detach import monitor_heartbeat_stale

    missing = str(tmp_path / "gone")
    assert monitor_heartbeat_stale(missing, poll_s=60) is True
    hb = tmp_path / "hb"
    hb.write_text("x", encoding="utf-8")
    assert monitor_heartbeat_stale(str(hb), poll_s=60) is False
    os.utime(hb, (time.time() - 200, time.time() - 200))
    assert monitor_heartbeat_stale(str(hb), poll_s=60) is True
