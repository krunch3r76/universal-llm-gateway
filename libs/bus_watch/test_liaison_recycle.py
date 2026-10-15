"""Tests for scripts/liaison-recycle-ticker.sh (fixture-only; no live signals)."""

from __future__ import annotations

import hashlib
import json
import os
import stat
import subprocess
import textwrap
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
_SCRIPT = _REPO / "scripts" / "liaison-recycle-ticker.sh"
_PY = Path.home() / ".venvs" / "universal" / "bin" / "python"


def _run_recycle(
    tmp_path: Path,
    *,
    root: str,
    env: dict[str, str] | None = None,
    extra_args: list[str] | None = None,
) -> subprocess.CompletedProcess[str]:
    run_env = {
        **os.environ,
        "EMAIL_BRIDGE_SOCK": "/nonexistent",
        "WATCH_DIR": str(tmp_path / "watchers"),
        "LIAISON_HUB_ROOT": str(_REPO),
        "LIAISON_PYTHON": str(_PY),
    }
    if env:
        run_env.update(env)
    (tmp_path / "watchers").mkdir(parents=True, exist_ok=True)
    cmd = [str(_SCRIPT), "--root", root, *(extra_args or [])]
    return subprocess.run(
        cmd,
        cwd=_REPO,
        env=run_env,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )


def _write_fake_systemctl(
    bin_dir: Path,
    *,
    load_state: str = "",
) -> None:
    bin_dir.mkdir(parents=True, exist_ok=True)
    log_file = str(bin_dir / "systemctl.log")
    script = bin_dir / "systemctl"
    body = textwrap.dedent(
        f"""\
        #!/usr/bin/env bash
        set -eu
        LOG_FILE={log_file!r}
        printf '%s\\n' "$*" >>"$LOG_FILE"
        if [[ "$*" == *"LoadState"* ]]; then
          printf '%s\\n' {load_state!r}
          exit 0
        fi
        if [[ "$*" == *" restart "* ]] || [[ "$*" == *" start "* ]]; then
          exit 0
        fi
        if [[ "$*" == *reset-failed* ]]; then
          exit 0
        fi
        exit 0
        """
    )
    script.write_text(body, encoding="utf-8")
    script.chmod(script.stat().st_mode | stat.S_IXUSR)


def _write_fake_kill(bin_dir: Path, signal_log: Path) -> None:
    script = bin_dir / "kill"
    log_file = str(signal_log)
    script.write_text(
        textwrap.dedent(
            f"""\
            #!/usr/bin/env bash
            echo "$*" >>{log_file!r}
            exit 0
            """
        ),
        encoding="utf-8",
    )
    script.chmod(script.stat().st_mode | stat.S_IXUSR)


def _write_fake_systemd_run(bin_dir: Path) -> None:
    script = bin_dir / "systemd-run"
    log_file = str(bin_dir / "systemd-run.log")
    script.write_text(
        textwrap.dedent(
            f"""\
            #!/usr/bin/env bash
            echo "$*" >>{log_file!r}
            exit 0
            """
        ),
        encoding="utf-8",
    )
    script.chmod(script.stat().st_mode | stat.S_IXUSR)


def _fake_proc(tmp_path: Path, pid: int, cmdline: str) -> None:
    proc = tmp_path / "proc" / str(pid)
    proc.mkdir(parents=True)
    (proc / "cmdline").write_bytes(cmdline.replace(" ", "\0").encode() + b"\0")


def test_recycle_refuses_attended_register(tmp_path: Path) -> None:
    watch = tmp_path / "watchers"
    watch.mkdir()
    (watch / "liaison-16690.tick.json").write_text(
        json.dumps({"register": "attended"}), encoding="utf-8"
    )
    bin_dir = tmp_path / "bin"
    signal_log = tmp_path / "signals.log"
    _write_fake_systemctl(bin_dir, load_state="")
    _write_fake_kill(bin_dir, signal_log)
    _fake_proc(
        tmp_path,
        9001,
        "bash liaison-arm-loop.sh --root 16690 --register attended --loop",
    )
    proc = _run_recycle(
        tmp_path,
        root="16690",
        env={
            "PATH": f"{bin_dir}:{os.environ.get('PATH', '')}",
            "LIAISON_PROC_ROOT": str(tmp_path / "proc"),
            "LIAISON_SIGNAL_LOG": str(signal_log),
        },
    )
    assert proc.returncode == 2
    assert not signal_log.exists()


def test_recycle_restarts_loaded_unit_without_kill(tmp_path: Path) -> None:
    bin_dir = tmp_path / "bin"
    signal_log = tmp_path / "signals.log"
    _write_fake_systemctl(bin_dir, load_state="loaded")
    _write_fake_kill(bin_dir, signal_log)
    proc = _run_recycle(
        tmp_path,
        root="10534",
        env={
            "PATH": f"{bin_dir}:{os.environ.get('PATH', '')}",
            "LIAISON_PROC_ROOT": str(tmp_path / "proc"),
            "LIAISON_SIGNAL_LOG": str(signal_log),
        },
    )
    assert proc.returncode == 0
    log = (bin_dir / "systemctl.log").read_text(encoding="utf-8")
    assert "restart liaison-ticker-10534.service" in log
    assert not signal_log.exists()


def test_recycle_orphan_sigterm_then_systemd_run(tmp_path: Path) -> None:
    root = "16401"
    watch = tmp_path / "watchers"
    watch.mkdir()
    lock = watch / f"liaison-ticker-{root}.lock"
    lock.write_text(
        json.dumps({"holder": f"ticker:{root}", "holder_pid": 999999}),
        encoding="utf-8",
    )
    bin_dir = tmp_path / "bin"
    signal_log = tmp_path / "signals.log"
    _write_fake_systemctl(bin_dir, load_state="")
    _write_fake_kill(bin_dir, signal_log)
    _write_fake_systemd_run(bin_dir)
    _fake_proc(
        tmp_path,
        5001,
        f"{_PY} scripts/liaison-tick.py --root {root} --loop --spawn-on-wake",
    )
    proc = _run_recycle(
        tmp_path,
        root=root,
        env={
            "PATH": f"{bin_dir}:{os.environ.get('PATH', '')}",
            "LIAISON_PROC_ROOT": str(tmp_path / "proc"),
            "LIAISON_SIGNAL_LOG": str(signal_log),
        },
    )
    assert proc.returncode == 0
    assert signal_log.read_text(encoding="utf-8").count("TERM") == 1
    ctl = (bin_dir / "systemctl.log").read_text(encoding="utf-8")
    assert "reset-failed liaison-ticker-16401.service" in ctl
    assert "restart" not in ctl
    run_log = (bin_dir / "systemd-run.log").read_text(encoding="utf-8")
    assert "liaison-ticker-16401" in run_log
    assert "liaison-tick.py" in run_log and "16401" in run_log


def test_recycle_two_orphans_exit_3(tmp_path: Path) -> None:
    root = "16416"
    bin_dir = tmp_path / "bin"
    _write_fake_systemctl(bin_dir, load_state="")
    _write_fake_kill(bin_dir, tmp_path / "signals.log")
    _write_fake_systemd_run(bin_dir)
    _fake_proc(
        tmp_path,
        6001,
        f"python scripts/liaison-tick.py --root {root} --loop --spawn-on-wake",
    )
    _fake_proc(
        tmp_path,
        6002,
        f"python scripts/liaison-tick.py --root {root} --loop --spawn-on-wake",
    )
    proc = _run_recycle(
        tmp_path,
        root=root,
        env={
            "PATH": f"{bin_dir}:{os.environ.get('PATH', '')}",
            "LIAISON_PROC_ROOT": str(tmp_path / "proc"),
        },
    )
    assert proc.returncode == 3
    assert not (bin_dir / "systemd-run.log").exists()


def test_recycle_dry_run_leaves_tick_and_lease_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = "10534"
    watch = tmp_path / "watchers"
    watch.mkdir()
    tick = watch / f"liaison-{root}.tick.json"
    tick.write_text('{"register":"autonomous","ticks":9}\n', encoding="utf-8")
    tick_hash = hashlib.sha256(tick.read_bytes()).hexdigest()
    lease = watch / f"liaison-ticker-{root}.lock"
    lease.write_text(
        json.dumps({"holder": f"ticker:{root}", "holder_pid": 1}),
        encoding="utf-8",
    )
    lease_hash = hashlib.sha256(lease.read_bytes()).hexdigest()
    bin_dir = tmp_path / "bin"
    _write_fake_systemctl(bin_dir, load_state="loaded")
    proc = _run_recycle(
        tmp_path,
        root=root,
        extra_args=["--dry-run"],
        env={
            "PATH": f"{bin_dir}:{os.environ.get('PATH', '')}",
            "LIAISON_PROC_ROOT": str(tmp_path / "proc"),
        },
    )
    assert proc.returncode == 0
    assert hashlib.sha256(tick.read_bytes()).hexdigest() == tick_hash
    assert hashlib.sha256(lease.read_bytes()).hexdigest() == lease_hash
    assert "restart" not in (bin_dir / "systemctl.log").read_text(encoding="utf-8")
