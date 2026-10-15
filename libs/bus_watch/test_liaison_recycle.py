"""Tests for scripts/liaison-recycle-ticker.sh (fixture-only; no live signals)."""

from __future__ import annotations

import hashlib
import json
import os
import stat
import subprocess
import textwrap
import threading
import time
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
    load_state: str = "not-found",
    main_pid: str = "0",
    unreachable: bool = False,
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
        if [[ "$*" == *"Version"* ]]; then
          if [[ {unreachable!r} == "True" ]]; then
            echo "Failed to connect to bus" >&2
            exit 1
          fi
          printf '252\\n'
          exit 0
        fi
        if [[ "$*" == *"LoadState"* ]]; then
          printf '%s\\n' {load_state!r}
          exit 0
        fi
        if [[ "$*" == *"MainPID"* ]]; then
          printf '%s\\n' {main_pid!r}
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


def _write_fake_kill(
    bin_dir: Path,
    signal_log: Path,
    *,
    clear_proc: bool = True,
    clear_lock: bool = True,
    delay_clear_proc_s: float = 0.0,
    exit_code: int = 0,
) -> None:
    bin_dir.mkdir(parents=True, exist_ok=True)
    script = bin_dir / "kill"
    log_file = str(signal_log)
    script.write_text(
        textwrap.dedent(
            f"""\
            #!/usr/bin/env bash
            set -eu
            echo "KILL_SENT $*" >>{log_file!r}
            if [[ "$1" == "-TERM" || "$1" == "-SIGTERM" || "$1" == "TERM" ]]; then
              target="$2"
              delay={delay_clear_proc_s}
              if [[ "$delay" != "0.0" && "$delay" != "0" ]]; then
                if [[ -n "${{LIAISON_PROC_ROOT:-}}" && -n "$target" ]]; then
                  ( sleep "$delay"; rm -rf "${{LIAISON_PROC_ROOT}}/$target" ) &
                fi
              elif [[ "{clear_proc}" == "True" && -n "${{LIAISON_PROC_ROOT:-}}" && -n "$target" ]]; then
                rm -rf "${{LIAISON_PROC_ROOT}}/$target" 2>/dev/null || true
              fi
              if [[ "{clear_lock}" == "True" && -n "${{WATCH_DIR:-}}" && -n "${{ROOT:-}}" ]]; then
                lock="${{WATCH_DIR}}/liaison-ticker-${{ROOT}}.lock"
                if [[ -f "$lock" ]]; then
                  printf '%s\\n' '{{"holder": null, "root": "'"${{ROOT}}"'}}' >"$lock"
                fi
              fi
            fi
            exit {exit_code}
            """
        ),
        encoding="utf-8",
    )
    script.chmod(script.stat().st_mode | stat.S_IXUSR)


def _write_fake_systemd_run(
    bin_dir: Path,
    *,
    fail_if_proc_exists: str = "",
) -> None:
    bin_dir.mkdir(parents=True, exist_ok=True)
    script = bin_dir / "systemd-run"
    log_file = str(bin_dir / "systemd-run.log")
    fail_pid = fail_if_proc_exists
    script.write_text(
        textwrap.dedent(
            f"""\
            #!/usr/bin/env bash
            set -eu
            echo "$*" >>{log_file!r}
            if [[ -n {fail_pid!r} && -n "${{LIAISON_PROC_ROOT:-}}" ]]; then
              if [[ -d "${{LIAISON_PROC_ROOT}}/{fail_pid}" ]]; then
                exit 99
              fi
            fi
            exit 0
            """
        ),
        encoding="utf-8",
    )
    script.chmod(script.stat().st_mode | stat.S_IXUSR)


def _write_delayed_proc_removal_kill(
    bin_dir: Path,
    signal_log: Path,
    proc_root: Path,
    pid: int,
    delay_s: float,
) -> None:
    """Fake kill that removes proc after delay; never touches lock bytes."""

    def _delayed_rm() -> None:
        time.sleep(delay_s)
        import shutil

        shutil.rmtree(proc_root / str(pid), ignore_errors=True)

    bin_dir.mkdir(parents=True, exist_ok=True)
    log_file = str(signal_log)
    script = bin_dir / "kill"
    script.write_text(
        textwrap.dedent(
            f"""\
            #!/usr/bin/env bash
            set -eu
            echo "KILL_SENT $*" >>{log_file!r}
            exit 0
            """
        ),
        encoding="utf-8",
    )
    script.chmod(script.stat().st_mode | stat.S_IXUSR)
    threading.Thread(target=_delayed_rm, daemon=True).start()


def _fake_proc(tmp_path: Path, pid: int, cmdline: str) -> None:
    proc = tmp_path / "proc" / str(pid)
    proc.mkdir(parents=True)
    (proc / "cmdline").write_bytes(cmdline.replace(" ", "\0").encode() + b"\0")


def _bin_env(bin_dir: Path, proc_root: Path, **extra: str) -> dict[str, str]:
    kill = str(bin_dir / "kill")
    return {
        "PATH": f"{bin_dir}:{os.environ.get('PATH', '')}",
        "LIAISON_PROC_ROOT": str(proc_root),
        "LIAISON_KILL": kill,
        **extra,
    }


def test_recycle_refuses_attended_register(tmp_path: Path) -> None:
    watch = tmp_path / "watchers"
    watch.mkdir()
    (watch / "liaison-16690.tick.json").write_text(
        json.dumps({"register": "attended"}), encoding="utf-8"
    )
    bin_dir = tmp_path / "bin"
    signal_log = tmp_path / "signals.log"
    _write_fake_systemctl(bin_dir, load_state="not-found")
    _write_fake_kill(bin_dir, signal_log)
    _fake_proc(
        tmp_path,
        9001,
        "bash liaison-arm-loop.sh --root 16690 --register attended --loop",
    )
    proc = _run_recycle(
        tmp_path,
        root="16690",
        env=_bin_env(bin_dir, tmp_path / "proc"),
    )
    assert proc.returncode == 2
    assert not signal_log.exists()


def test_recycle_restarts_loaded_unit_without_kill(tmp_path: Path) -> None:
    bin_dir = tmp_path / "bin"
    signal_log = tmp_path / "signals.log"
    _write_fake_systemctl(bin_dir, load_state="loaded", main_pid="8888")
    _write_fake_kill(bin_dir, signal_log)
    _fake_proc(
        tmp_path,
        8888,
        f"{_PY} scripts/liaison-tick.py --root 10534 --loop --spawn-on-wake",
    )
    proc = _run_recycle(
        tmp_path,
        root="10534",
        env=_bin_env(bin_dir, tmp_path / "proc"),
    )
    assert proc.returncode == 0
    log = (bin_dir / "systemctl.log").read_text(encoding="utf-8")
    assert "restart liaison-ticker-10534.service" in log
    assert not signal_log.exists()


def test_recycle_restarts_loaded_unit_with_mainpid_in_proc(tmp_path: Path) -> None:
    bin_dir = tmp_path / "bin"
    signal_log = tmp_path / "signals.log"
    _write_fake_systemctl(bin_dir, load_state="loaded", main_pid="7777")
    _write_fake_kill(bin_dir, signal_log)
    _fake_proc(
        tmp_path,
        7777,
        f"{_PY} scripts/liaison-tick.py --root 10534 --loop --spawn-on-wake",
    )
    proc = _run_recycle(
        tmp_path,
        root="10534",
        env=_bin_env(bin_dir, tmp_path / "proc"),
    )
    assert proc.returncode == 0
    assert "restart liaison-ticker-10534.service" in (
        bin_dir / "systemctl.log"
    ).read_text(encoding="utf-8")


def test_recycle_loaded_unit_plus_orphan_exit_3(tmp_path: Path) -> None:
    bin_dir = tmp_path / "bin"
    _write_fake_systemctl(bin_dir, load_state="loaded", main_pid="8888")
    _write_fake_kill(bin_dir, tmp_path / "signals.log")
    _fake_proc(
        tmp_path,
        8888,
        f"{_PY} scripts/liaison-tick.py --root 10534 --loop --spawn-on-wake",
    )
    _fake_proc(
        tmp_path,
        9002,
        f"{_PY} scripts/liaison-tick.py --root 10534 --loop --spawn-on-wake",
    )
    proc = _run_recycle(
        tmp_path,
        root="10534",
        env=_bin_env(bin_dir, tmp_path / "proc"),
    )
    assert proc.returncode == 3
    assert "orphan" in proc.stderr.lower()


def test_recycle_orphan_sigterm_then_systemd_run(tmp_path: Path) -> None:
    root = "16401"
    orphan_pid = 999991
    watch = tmp_path / "watchers"
    watch.mkdir()
    lock = watch / f"liaison-ticker-{root}.lock"
    lock.write_text(
        json.dumps({"holder": f"ticker:{root}", "holder_pid": orphan_pid}),
        encoding="utf-8",
    )
    bin_dir = tmp_path / "bin"
    signal_log = tmp_path / "signals.log"
    _write_fake_systemctl(bin_dir, load_state="not-found")
    _write_fake_kill(bin_dir, signal_log)
    _write_fake_systemd_run(bin_dir)
    _fake_proc(
        tmp_path,
        orphan_pid,
        f"{_PY} scripts/liaison-tick.py --root {root} --loop --spawn-on-wake",
    )
    proc = _run_recycle(
        tmp_path,
        root=root,
        env=_bin_env(bin_dir, tmp_path / "proc", ROOT=root),
    )
    assert proc.returncode == 0
    log_text = signal_log.read_text(encoding="utf-8")
    assert "KILL_SENT -TERM" in log_text
    ctl = (bin_dir / "systemctl.log").read_text(encoding="utf-8")
    assert "reset-failed liaison-ticker-16401.service" in ctl
    assert "restart" not in ctl
    run_log = (bin_dir / "systemd-run.log").read_text(encoding="utf-8")
    assert "liaison-ticker-16401" in run_log
    assert "liaison-tick.py" in run_log and "16401" in run_log


def test_recycle_orphan_systemd_run_failure_after_sigterm(tmp_path: Path) -> None:
    root = "16401"
    orphan_pid = 999991
    watch = tmp_path / "watchers"
    watch.mkdir()
    (watch / f"liaison-ticker-{root}.lock").write_text(
        json.dumps({"holder": f"ticker:{root}", "holder_pid": orphan_pid}),
        encoding="utf-8",
    )
    bin_dir = tmp_path / "bin"
    signal_log = tmp_path / "signals.log"
    _write_fake_systemctl(bin_dir, load_state="not-found")
    _write_fake_kill(bin_dir, signal_log)
    run_script = bin_dir / "systemd-run"
    run_script.write_text(
        "#!/usr/bin/env bash\nexit 1\n",
        encoding="utf-8",
    )
    run_script.chmod(run_script.stat().st_mode | stat.S_IXUSR)
    _fake_proc(
        tmp_path,
        orphan_pid,
        f"{_PY} scripts/liaison-tick.py --root {root} --loop --spawn-on-wake",
    )
    proc = _run_recycle(
        tmp_path,
        root=root,
        env=_bin_env(bin_dir, tmp_path / "proc", ROOT=root),
    )
    assert proc.returncode != 0
    assert "KILL_SENT -TERM" in signal_log.read_text(encoding="utf-8")
    assert "orphan" in proc.stderr.lower()


def test_recycle_orphan_lease_timeout_still_attempts_systemd_run(
    tmp_path: Path,
) -> None:
    root = "16401"
    orphan_pid = 999992
    watch = tmp_path / "watchers"
    watch.mkdir()
    lock = watch / f"liaison-ticker-{root}.lock"
    lock.write_text(
        json.dumps(
            {
                "holder": f"ticker:{root}",
                "holder_pid": os.getpid(),
                "root": root,
            }
        ),
        encoding="utf-8",
    )
    bin_dir = tmp_path / "bin"
    signal_log = tmp_path / "signals.log"
    _write_fake_systemctl(bin_dir, load_state="not-found")
    _write_fake_kill(
        bin_dir, signal_log, clear_proc=False, clear_lock=False
    )
    _write_fake_systemd_run(bin_dir)
    _fake_proc(
        tmp_path,
        orphan_pid,
        f"{_PY} scripts/liaison-tick.py --root {root} --loop --spawn-on-wake",
    )
    proc = _run_recycle(
        tmp_path,
        root=root,
        env=_bin_env(
            bin_dir,
            tmp_path / "proc",
            LIAISON_REAP_TIMEOUT_S="0.1",
            ROOT=root,
        ),
    )
    assert proc.returncode == 4
    assert "timeout" in proc.stderr.lower() or "reap" in proc.stderr.lower()
    assert (bin_dir / "systemd-run.log").exists()


def test_recycle_waits_until_orphan_reaped_before_systemd_run(
    tmp_path: Path,
) -> None:
    root = "16401"
    orphan_pid = 999991
    watch = tmp_path / "watchers"
    watch.mkdir()
    lock = watch / f"liaison-ticker-{root}.lock"
    free_lock = json.dumps({"holder": None, "root": root})
    lock.write_text(free_lock, encoding="utf-8")
    lock_hash = hashlib.sha256(lock.read_bytes()).hexdigest()
    bin_dir = tmp_path / "bin"
    signal_log = tmp_path / "signals.log"
    proc_root = tmp_path / "proc"
    _write_fake_systemctl(bin_dir, load_state="not-found")
    _write_fake_kill(
        bin_dir,
        signal_log,
        clear_proc=False,
        clear_lock=False,
        delay_clear_proc_s=0.15,
    )
    _write_fake_systemd_run(bin_dir, fail_if_proc_exists=str(orphan_pid))
    _fake_proc(
        tmp_path,
        orphan_pid,
        f"{_PY} scripts/liaison-tick.py --root {root} --loop --spawn-on-wake",
    )
    proc = _run_recycle(
        tmp_path,
        root=root,
        env=_bin_env(bin_dir, proc_root, ROOT=root),
    )
    assert proc.returncode == 0
    assert hashlib.sha256(lock.read_bytes()).hexdigest() == lock_hash


def test_recycle_zero_orphans_still_systemd_run(tmp_path: Path) -> None:
    root = "16401"
    bin_dir = tmp_path / "bin"
    _write_fake_systemctl(bin_dir, load_state="not-found")
    _write_fake_kill(bin_dir, tmp_path / "signals.log")
    _write_fake_systemd_run(bin_dir)
    proc = _run_recycle(
        tmp_path,
        root=root,
        env=_bin_env(bin_dir, tmp_path / "proc"),
    )
    assert proc.returncode == 0
    assert "cold_start" in proc.stdout
    assert (bin_dir / "systemd-run.log").exists()


def test_recycle_rejects_invalid_root(tmp_path: Path) -> None:
    bin_dir = tmp_path / "bin"
    _write_fake_systemctl(bin_dir)
    proc = _run_recycle(
        tmp_path,
        root="not-a-root",
        env={"PATH": f"{bin_dir}:{os.environ.get('PATH', '')}"},
    )
    assert proc.returncode == 1
    proc2 = _run_recycle(
        tmp_path,
        root="10534abc",
        env={"PATH": f"{bin_dir}:{os.environ.get('PATH', '')}"},
    )
    assert proc2.returncode == 1


def test_recycle_attended_tick_parse_error_exit_2(tmp_path: Path) -> None:
    watch = tmp_path / "watchers"
    watch.mkdir()
    (watch / "liaison-10534.tick.json").write_text("{not json", encoding="utf-8")
    bin_dir = tmp_path / "bin"
    _write_fake_systemctl(bin_dir)
    proc = _run_recycle(
        tmp_path,
        root="10534",
        env={
            "PATH": f"{bin_dir}:{os.environ.get('PATH', '')}",
            "LIAISON_PROC_ROOT": str(tmp_path / "proc"),
        },
    )
    assert proc.returncode == 2


def test_recycle_attended_loop_without_tick_exit_2(tmp_path: Path) -> None:
    bin_dir = tmp_path / "bin"
    _write_fake_systemctl(bin_dir)
    _write_fake_kill(bin_dir, tmp_path / "signals.log")
    _fake_proc(
        tmp_path,
        9100,
        f"{_PY} scripts/liaison-tick.py --root 16690 --loop",
    )
    proc = _run_recycle(
        tmp_path,
        root="16690",
        env=_bin_env(bin_dir, tmp_path / "proc"),
    )
    assert proc.returncode == 2


def test_recycle_two_orphans_exit_3(tmp_path: Path) -> None:
    root = "16416"
    bin_dir = tmp_path / "bin"
    _write_fake_systemctl(bin_dir, load_state="not-found")
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
        env=_bin_env(bin_dir, tmp_path / "proc"),
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
    assert "dry_run:" in proc.stdout
    assert hashlib.sha256(tick.read_bytes()).hexdigest() == tick_hash
    assert hashlib.sha256(lease.read_bytes()).hexdigest() == lease_hash
    assert "restart" not in (bin_dir / "systemctl.log").read_text(encoding="utf-8")


def test_recycle_user_manager_unreachable_exit_2(tmp_path: Path) -> None:
    bin_dir = tmp_path / "bin"
    signal_log = tmp_path / "signals.log"
    _write_fake_systemctl(bin_dir, load_state="not-found", unreachable=True)
    _write_fake_kill(bin_dir, signal_log)
    _fake_proc(
        tmp_path,
        8888,
        f"{_PY} scripts/liaison-tick.py --root 10534 --loop --spawn-on-wake",
    )
    proc = _run_recycle(
        tmp_path,
        root="10534",
        env=_bin_env(bin_dir, tmp_path / "proc"),
    )
    assert proc.returncode == 2
    assert "user_manager_unreachable" in proc.stderr
    assert not signal_log.exists()


def test_spawn_scan_rejects_prefix_root_id(tmp_path: Path) -> None:
    bin_dir = tmp_path / "bin"
    _write_fake_systemctl(bin_dir)
    _write_fake_kill(bin_dir, tmp_path / "signals.log")
    _write_fake_systemd_run(bin_dir)
    _fake_proc(
        tmp_path,
        7001,
        f"{_PY} scripts/liaison-tick.py --root 61640 --loop --spawn-on-wake",
    )
    proc = _run_recycle(
        tmp_path,
        root="6164",
        env=_bin_env(bin_dir, tmp_path / "proc"),
    )
    assert proc.returncode == 0
    assert "cold_start" in proc.stdout
    assert not (bin_dir / "systemd-run.log").read_text(encoding="utf-8").count(
        "61640"
    )


def test_recycle_orphan_timeout_exits_nonzero_still_systemd_run(
    tmp_path: Path,
) -> None:
    root = "16401"
    orphan_pid = 999992
    watch = tmp_path / "watchers"
    watch.mkdir()
    (watch / f"liaison-ticker-{root}.lock").write_text(
        json.dumps(
            {"holder": f"ticker:{root}", "holder_pid": os.getpid(), "root": root}
        ),
        encoding="utf-8",
    )
    bin_dir = tmp_path / "bin"
    _write_fake_systemctl(bin_dir)
    _write_fake_kill(
        bin_dir, tmp_path / "signals.log", clear_proc=False, clear_lock=False
    )
    _write_fake_systemd_run(bin_dir)
    _fake_proc(
        tmp_path,
        orphan_pid,
        f"{_PY} scripts/liaison-tick.py --root {root} --loop --spawn-on-wake",
    )
    proc = _run_recycle(
        tmp_path,
        root=root,
        env=_bin_env(
            bin_dir, tmp_path / "proc", LIAISON_REAP_TIMEOUT_S="0.1", ROOT=root
        ),
    )
    assert proc.returncode != 0
    assert (bin_dir / "systemd-run.log").exists()


def test_recycle_kill_failure_not_silent(tmp_path: Path) -> None:
    root = "16401"
    orphan_pid = 999991
    bin_dir = tmp_path / "bin"
    _write_fake_systemctl(bin_dir)
    _write_fake_kill(
        bin_dir, tmp_path / "signals.log", exit_code=1
    )
    _fake_proc(
        tmp_path,
        orphan_pid,
        f"{_PY} scripts/liaison-tick.py --root {root} --loop --spawn-on-wake",
    )
    proc = _run_recycle(
        tmp_path,
        root=root,
        env=_bin_env(bin_dir, tmp_path / "proc", ROOT=root),
    )
    assert proc.returncode != 0
    assert "kill_failed" in proc.stderr.lower()


def test_recycle_orphan_sigterm_waits_before_cold_start(tmp_path: Path) -> None:
    root = "16401"
    orphan_pid = 999991
    watch = tmp_path / "watchers"
    watch.mkdir()
    (watch / f"liaison-ticker-{root}.lock").write_text(
        json.dumps({"holder": None, "root": root}),
        encoding="utf-8",
    )
    bin_dir = tmp_path / "bin"
    signal_log = tmp_path / "signals.log"
    proc_root = tmp_path / "proc"
    _write_fake_systemctl(bin_dir)
    _write_fake_kill(
        bin_dir,
        signal_log,
        clear_proc=False,
        clear_lock=False,
        delay_clear_proc_s=0.3,
    )
    _write_fake_systemd_run(bin_dir, fail_if_proc_exists=str(orphan_pid))
    _fake_proc(
        tmp_path,
        orphan_pid,
        f"{_PY} scripts/liaison-tick.py --root {root} --loop --spawn-on-wake",
    )
    proc = _run_recycle(
        tmp_path,
        root=root,
        env=_bin_env(bin_dir, proc_root, ROOT=root),
    )
    assert proc.returncode == 0
    assert "KILL_SENT" in signal_log.read_text(encoding="utf-8")
    assert "cold_start" not in proc.stdout


def test_recycle_pre_kill_requires_ticker_lease_live_api(tmp_path: Path) -> None:
    stub = tmp_path / "stub_libs"
    (stub / "bus_watch").mkdir(parents=True)
    (stub / "bus_watch" / "__init__.py").write_text("", encoding="utf-8")
    (stub / "bus_watch" / "fable_lock.py").write_text(
        "def read_ticker_lock(root):\n    return {}\n",
        encoding="utf-8",
    )
    root = "16401"
    orphan_pid = 999991
    bin_dir = tmp_path / "bin"
    signal_log = tmp_path / "signals.log"
    _write_fake_systemctl(bin_dir)
    _write_fake_kill(bin_dir, signal_log)
    _fake_proc(
        tmp_path,
        orphan_pid,
        f"{_PY} scripts/liaison-tick.py --root {root} --loop --spawn-on-wake",
    )
    proc = _run_recycle(
        tmp_path,
        root=root,
        env=_bin_env(
            bin_dir,
            tmp_path / "proc",
            ROOT=root,
            LIAISON_PYTHONPATH=str(stub),
        ),
    )
    assert proc.returncode == 2
    assert not signal_log.exists()


def test_dry_run_prints_branch_loaded(tmp_path: Path) -> None:
    bin_dir = tmp_path / "bin"
    _write_fake_systemctl(bin_dir, load_state="loaded", main_pid="0")
    proc = _run_recycle(
        tmp_path,
        root="10534",
        extra_args=["--dry-run"],
        env={
            "PATH": f"{bin_dir}:{os.environ.get('PATH', '')}",
            "LIAISON_PROC_ROOT": str(tmp_path / "proc"),
        },
    )
    assert proc.returncode == 0
    assert "dry_run: loaded_restart" in proc.stdout


def test_dry_run_prints_branch_orphan(tmp_path: Path) -> None:
    root = "16401"
    bin_dir = tmp_path / "bin"
    _write_fake_systemctl(bin_dir)
    _write_fake_kill(bin_dir, tmp_path / "signals.log")
    _fake_proc(
        tmp_path,
        999991,
        f"{_PY} scripts/liaison-tick.py --root {root} --loop --spawn-on-wake",
    )
    proc = _run_recycle(
        tmp_path,
        root=root,
        extra_args=["--dry-run"],
        env=_bin_env(bin_dir, tmp_path / "proc"),
    )
    assert proc.returncode == 0
    assert "dry_run: orphan_recycle" in proc.stdout
