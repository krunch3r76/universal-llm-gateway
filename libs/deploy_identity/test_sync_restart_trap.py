"""Trap integration tests for scripts/sync-and-restart-mcp.sh (stub docker on PATH)."""

from __future__ import annotations

import os
import signal
import stat
import subprocess
import textwrap
from pathlib import Path

import pytest

pytestmark = pytest.mark.offline

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SCRIPT = _REPO_ROOT / "scripts" / "sync-and-restart-mcp.sh"


def _stub_docker_dir(
    tmp_path: Path,
    *,
    stop_fails: bool = False,
    start_fails: bool = False,
    inspect_health: str = "healthy",
    post_restart_health: str | None = None,
    container_exists: bool = True,
    kill_on_run: bool = False,
    kill_on_stop: bool = False,
    import_probe_fails: bool = False,
    compose_fails: bool = False,
    kill_before_stamp_write: bool = False,
    stamp_exec_fails: bool = False,
    stamp_restore_fails: bool = False,
    exec_true_fails: bool = False,
    rollback_services_cp_fails: bool = False,
    kill_on_sync_cp: bool = False,
    materialize_host_cp: bool = True,
    nonce_mode: str = "stored",
    prior_stamp: str = "2020-01-01T00:00:00Z\noldsha\n",
) -> Path:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "docker.log"
    nonce_file = tmp_path / "nonce"
    stamp_file = tmp_path / "container_stamp"
    health_phase = tmp_path / "post_restart"
    prior_stamp_file = tmp_path / "prior_stamp"
    prior_stamp_file.write_text(prior_stamp, encoding="utf-8")
    script = textwrap.dedent(
        f"""\
        #!/usr/bin/env bash
        echo "$@" >> {log}
        health_phase="{health_phase}"
        stamp_file="{stamp_file}"
        prior_stamp_file="{prior_stamp_file}"
        case "$1" in
          ps)
            if [[ "$2" == "-a" ]]; then
              if [[ "{str(container_exists).lower()}" == "true" ]]; then
                echo "mcp-server"
              fi
              exit 0
            fi
            if [[ "{str(container_exists).lower()}" == "true" ]]; then
              echo "mcp-server"
            fi
            exit 0
            ;;
          inspect)
            if [[ "$*" == *"Health.Status"* ]] || [[ "$*" == *"State.Health"* ]]; then
              if [[ -f "$health_phase" && "{post_restart_health or ''}" != "" ]]; then
                echo "{post_restart_health or inspect_health}"
              else
                echo "{inspect_health}"
              fi
              exit 0
            fi
            if [[ "$*" == *".Image"* ]]; then
              echo "sha256:deadbeef"
              exit 0
            fi
            echo "running"
            exit 0
            ;;
          stop)
            if [[ "{str(kill_on_stop).lower()}" == "true" ]]; then
              kill -TERM "$PPID" 2>/dev/null || true
            fi
            if [[ "{str(stop_fails).lower()}" == "true" ]]; then
              exit 1
            fi
            exit 0
            ;;
          start)
            if [[ "{str(start_fails).lower()}" == "true" ]]; then
              exit 1
            fi
            printf '%s' post_restart > "$health_phase"
            exit 0
            ;;
          run)
            if [[ "{str(import_probe_fails).lower()}" == "true" && "$*" == *"--network"*"none"* ]]; then
              exit 1
            fi
            if [[ "{str(kill_on_run).lower()}" == "true" ]]; then
              script_pid=$(ps -o ppid= -p "$PPID" | tr -d ' ')
              if [[ -r "/proc/${{script_pid}}/cmdline" ]] && grep -a -q sync-and-restart "/proc/${{script_pid}}/cmdline"; then
                kill -TERM "$script_pid" 2>/dev/null || true
              fi
            fi
            exit 0
            ;;
          exec)
            joined="$*"
            nonce_file={str(nonce_file)!r}
            if [[ "$joined" == *" true" ]]; then
              if [[ "{str(exec_true_fails).lower()}" == "true" ]]; then
                exit 1
              fi
              exit 0
            fi
            if [[ "$joined" == *"test -f /app/.source_sync_stamp"* ]]; then
              if [[ -f "$stamp_file" || -s "$prior_stamp_file" ]]; then
                exit 0
              fi
              exit 1
            fi
            if [[ "$joined" == *"cat /app/.source_sync_stamp"* ]]; then
              if [[ -f "$stamp_file" ]]; then
                cat "$stamp_file"
              elif [[ -f "$prior_stamp_file" ]]; then
                cat "$prior_stamp_file"
              fi
              exit 0
            fi
            if [[ "$joined" == *".source_sync_stamp"* && "$joined" == *"printf"* ]]; then
              if [[ "{str(kill_before_stamp_write).lower()}" == "true" ]]; then
                kill -PIPE "$PPID" 2>/dev/null || true
                exit 1
              fi
              if [[ "{str(stamp_exec_fails).lower()}" == "true" ]]; then
                exit 1
              fi
              echo STAMP_WRITE >> {log}
              needle="> /app/.source_sync_stamp"
              if [[ "$joined" == *"$needle"* ]]; then
                rest="${{joined#*printf}}"
                printf '%s' "$rest" > "$stamp_file"
              fi
            fi
            if [[ "$joined" == *".source_sync_stamp"* && "$joined" == *"cat > /app/.source_sync_stamp"* ]]; then
              if [[ "{str(stamp_restore_fails).lower()}" == "true" ]]; then
                exit 1
              fi
              echo STAMP_RESTORE >> {log}
              cat > "$stamp_file"
            fi
            if [[ "$joined" == *".source_sync_nonce"* ]]; then
              if [[ "$joined" == *"cat /app/.source_sync_nonce"* ]]; then
                if [[ "{nonce_mode}" == "wrong" ]]; then
                  printf '%s\\n' "not-the-nonce"
                elif [[ "{nonce_mode}" == "stored" && -f "$nonce_file" ]]; then
                  cat "$nonce_file"
                fi
                exit 0
              fi
              needle="printf '%s\\\\n' '"
              rest="${{joined#*"$needle"}}"
              if [[ "$rest" != "$joined" ]]; then
                nonce=${{rest%%\\'*}}
                printf '%s\\n' "$nonce" > "$nonce_file"
              fi
            fi
            exit 0
            ;;
          compose)
            if [[ "{str(compose_fails).lower()}" == "true" ]]; then
              exit 1
            fi
            exit 0
            ;;
          cp)
            dest="${{@: -1}}"
            if [[ "{str(kill_on_sync_cp).lower()}" == "true" && "$dest" == "mcp-server:/app/libs/" && "$*" != *last-healthy* ]]; then
              kill -TERM "$PPID" 2>/dev/null || true
              exit 1
            fi
            if [[ "{str(rollback_services_cp_fails).lower()}" == "true" && "$dest" == "mcp-server:/app/services/" && "$*" == *last-healthy* ]]; then
              exit 1
            fi
            if [[ "{str(materialize_host_cp).lower()}" == "true" && "$dest" != *:* ]]; then
              if [[ "$dest" == *.py ]]; then
                mkdir -p "$(dirname "$dest")"
                printf '%s\\n' oldsha > "$dest"
              else
                mkdir -p "$dest"
                printf '%s\\n' oldsha > "$dest/marker"
              fi
            fi
            exit 0
            ;;
          tag|rm|image) exit 0 ;;
          *) exit 0 ;;
        esac
        """
    )
    stub = bin_dir / "docker"
    stub.write_text(script)
    stub.chmod(stub.stat().st_mode | stat.S_IXUSR)
    return bin_dir


def _minimal_env(tmp_path: Path, stub_bin: Path) -> dict[str, str]:
    mcp_yaml = tmp_path / "mcp.yaml"
    mcp_yaml.write_text(
        textwrap.dedent(
            f"""\
            auth_token: test-token
            project_dir: {tmp_path / "proj"}
            data_dir: {tmp_path / "mcp-data"}
            mcp_server_url: http://127.0.0.1/mcp
            """
        )
    )
    (tmp_path / "proj").mkdir()
    env = os.environ.copy()
    env["PATH"] = f"{stub_bin}:{env.get('PATH', '')}"
    env["MCP_YAML"] = str(mcp_yaml)
    env["HOME"] = str(tmp_path)
    env["EMAIL_BRIDGE_SOCK"] = "/nonexistent"
    env["PYTHONPATH"] = str(_REPO_ROOT / "libs")
    mktemp_dir = tmp_path / "mktemp"
    mktemp_dir.mkdir(exist_ok=True)
    env["TMPDIR"] = str(mktemp_dir)
    return env


def _python() -> str:
    return os.environ.get("PYTHON", str(Path.home() / ".venvs/universal/bin/python"))


def _run_sync_script(
    tmp_path: Path,
    stub: Path,
    *args: str,
    timeout: int = 180,
) -> subprocess.CompletedProcess[str]:
    env = _minimal_env(tmp_path, stub)
    env["PYTHON"] = _python()
    env["ULG_REPO"] = str(_REPO_ROOT)
    env["PROJECT_ROOT"] = str(_REPO_ROOT)
    first = env["PATH"].split(":")[0]
    if not os.access(Path(first) / "docker", os.X_OK):
        raise AssertionError(f"docker stub is not first on PATH: {first}")
    return subprocess.run(
        ["bash", str(_SCRIPT), *args],
        cwd=str(_REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


def _docker_log(tmp_path: Path) -> str:
    log = tmp_path / "docker.log"
    if not log.is_file():
        return ""
    return log.read_text(encoding="utf-8")


def _log_indices(log: str) -> dict[str, int]:
    return {
        "stamp_write": log.find("STAMP_WRITE"),
        "stamp_restore": log.find("STAMP_RESTORE"),
        "stop": log.find("stop "),
        "start": log.find("start "),
    }


def _seed_checked_image(tmp_path: Path) -> None:
    data = tmp_path / "mcp-data" / "last-healthy"
    data.mkdir(parents=True, exist_ok=True)
    (data / "checked_image_id").write_text("sha256:deadbeef\n", encoding="utf-8")


@pytest.mark.skipif(not _SCRIPT.is_file(), reason="script missing")
def test_routine_path_writes_stamp_before_stop_start(tmp_path: Path) -> None:
    """AC1: stamp write precedes docker stop/start and is not repeated after healthy."""
    stub = _stub_docker_dir(tmp_path, inspect_health="healthy", nonce_mode="stored")
    proc = _run_sync_script(tmp_path, stub)
    log = _docker_log(tmp_path)
    idx = _log_indices(log)
    assert proc.returncode == 0, proc.stderr
    assert idx["stamp_write"] >= 0, log
    assert log.count("STAMP_WRITE") == 1, log
    assert idx["stop"] >= 0 and idx["start"] >= 0, log
    assert idx["stamp_write"] < idx["stop"] < idx["start"], log


@pytest.mark.skipif(not _SCRIPT.is_file(), reason="script missing")
def test_stop_failure_restores_snapshotted_stamp(tmp_path: Path) -> None:
    """AC4: docker stop failure restores the pre-write stamp snapshot."""
    _seed_last_healthy_copy_set(tmp_path)
    stub = _stub_docker_dir(
        tmp_path,
        stop_fails=True,
        prior_stamp="2020-01-01T00:00:00Z\noldsha\n",
    )
    proc = _run_sync_script(tmp_path, stub)
    log = _docker_log(tmp_path)
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "STAMP_RESTORE" in log, log
    assert log.count("STAMP_WRITE") == 1, log
    assert "oldsha" in (tmp_path / "container_stamp").read_text(encoding="utf-8")
    assert "MCP_SYNC_LAST_HEALTHY_TREE_ROLLBACK" in proc.stderr


@pytest.mark.skipif(not _SCRIPT.is_file(), reason="script missing")
def test_start_failure_keeps_new_stamp_without_gate_restore(tmp_path: Path) -> None:
    """Start failure disarms EXIT trap; gate restore must not run (review condition 1)."""
    stub = _stub_docker_dir(tmp_path, start_fails=True, inspect_health="healthy")
    proc = _run_sync_script(tmp_path, stub)
    log = _docker_log(tmp_path)
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert log.count("STAMP_WRITE") == 1, log
    assert "STAMP_RESTORE" not in log, log
    assert "MCP_SYNC_GATE_RESTORE" not in proc.stderr + proc.stdout


@pytest.mark.skipif(not _SCRIPT.is_file(), reason="script missing")
def test_kill_during_stop_restores_snapshotted_stamp(tmp_path: Path) -> None:
    """TERM during docker stop, before stop is recorded, restores the snapshot."""
    _seed_last_healthy_copy_set(tmp_path)
    stub = _stub_docker_dir(
        tmp_path,
        kill_on_stop=True,
        prior_stamp="2020-01-01T00:00:00Z\noldsha\n",
    )
    proc = _run_sync_script(tmp_path, stub, timeout=90)
    log = _docker_log(tmp_path)
    assert proc.returncode == -signal.SIGTERM, (proc.returncode, proc.stderr[-400:])
    assert "STAMP_RESTORE" in log, log
    assert "oldsha" in (tmp_path / "container_stamp").read_text(encoding="utf-8")


@pytest.mark.skipif(not _SCRIPT.is_file(), reason="script missing")
def test_never_healthy_single_pre_restart_stamp_write(tmp_path: Path) -> None:
    """Never-healthy path: one pre-restart stamp write, then image restore (no post-healthy write)."""
    _seed_checked_image(tmp_path)
    stub = _stub_docker_dir(
        tmp_path,
        inspect_health="healthy",
        post_restart_health="unhealthy",
        nonce_mode="stored",
    )
    proc = _run_sync_script(tmp_path, stub)
    log = _docker_log(tmp_path)
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert log.count("STAMP_WRITE") == 1, log
    idx = _log_indices(log)
    assert idx["stamp_write"] < idx["stop"] < idx["start"], log
    assert "MCP_SYNC_GATE_RESTORE" in proc.stderr


def _seed_last_healthy_copy_set(tmp_path: Path, token: str = "oldsha") -> None:
    base = tmp_path / "mcp-data" / "last-healthy"
    for name in ("libs", "services", "config", "pipelines"):
        dest = base / name
        dest.mkdir(parents=True, exist_ok=True)
        (dest / "marker").write_text(token + "\n", encoding="utf-8")
    (base / "sitecustomize.py").write_text(token + "\n", encoding="utf-8")


def _stub_build_bash(stub: Path) -> None:
    stub_bash = stub / "bash"
    stub_bash.write_text(
        "#!/bin/bash\n"
        'if [[ "$1" == *build-mcp.sh* || "${2:-}" == *build-mcp.sh* ]]; then\n'
        "  exit 0\n"
        "fi\n"
        'exec /bin/bash "$@"\n'
    )
    stub_bash.chmod(stub_bash.stat().st_mode | stat.S_IXUSR)


@pytest.mark.skipif(not _SCRIPT.is_file(), reason="script missing")
def test_container_create_compose_failure_runs_gate_restore(tmp_path: Path) -> None:
    """Compose create failure runs gate restore."""
    _seed_checked_image(tmp_path)
    stub = _stub_docker_dir(
        tmp_path,
        container_exists=False,
        compose_fails=True,
        inspect_health="healthy",
    )
    proc = _run_sync_script(tmp_path, stub)
    log = _docker_log(tmp_path)
    assert proc.returncode != 0, proc.stdout
    assert "MCP_SYNC_GATE_RESTORE" in proc.stderr, proc.stderr
    assert "STAMP_RESTORE" not in log, log


@pytest.mark.skipif(not _SCRIPT.is_file(), reason="script missing")
def test_nocache_compose_up_failure_runs_gate_restore(tmp_path: Path) -> None:
    """--no-cache compose up failure after rm still runs gate restore."""
    _seed_checked_image(tmp_path)
    stub = _stub_docker_dir(
        tmp_path,
        container_exists=True,
        compose_fails=True,
        inspect_health="healthy",
    )
    _stub_build_bash(stub)
    env = _minimal_env(tmp_path, stub)
    env["PYTHON"] = _python()
    env["ULG_REPO"] = str(_REPO_ROOT)
    env["PROJECT_ROOT"] = str(_REPO_ROOT)
    proc = subprocess.run(
        ["bash", str(_SCRIPT), "--no-cache"],
        cwd=str(_REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    log = _docker_log(tmp_path)
    assert proc.returncode != 0, proc.stdout + proc.stderr
    assert "MCP_SYNC_GATE_RESTORE" in proc.stderr, proc.stderr[-800:]
    assert "STAMP_WRITE" not in log, log
    assert "STAMP_RESTORE" not in log, log


@pytest.mark.skipif(not _SCRIPT.is_file(), reason="script missing")
def test_concurrent_sync_exits_while_lock_held(tmp_path: Path) -> None:
    """A second run exits non-zero while the MCP_DATA_DIR lock is held."""
    lock = tmp_path / "mcp-data" / "sync-and-restart-mcp.lock"
    lock.parent.mkdir(parents=True, exist_ok=True)
    lock.touch()
    holder = subprocess.Popen(
        ["flock", str(lock), "bash", "-c", "echo ready; sleep 30"],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
    )
    assert holder.stdout is not None
    assert holder.stdout.readline().strip() == "ready"
    try:
        stub = _stub_docker_dir(tmp_path, inspect_health="healthy")
        proc = _run_sync_script(tmp_path, stub, timeout=30)
    finally:
        holder.kill()
        holder.wait(timeout=5)
    assert proc.returncode != 0, proc.stdout
    assert "another sync-and-restart-mcp.sh is already running" in proc.stderr


def _assert_mktemp_removed(tmp_path: Path) -> None:
    leftover = list((tmp_path / "mktemp").iterdir())
    assert leftover == [], leftover


def _assert_tree_rolled_back(log: str, tmp_path: Path) -> None:
    for name in ("libs", "services", "config", "pipelines"):
        assert f"{tmp_path}/mcp-data/last-healthy/{name}/." in log, log
        assert f"mcp-server:/app/{name}/" in log, log
    assert f"{tmp_path}/mcp-data/last-healthy/sitecustomize.py" in log, log
    assert "oldsha" in (tmp_path / "container_stamp").read_text(encoding="utf-8")


@pytest.mark.skipif(not _SCRIPT.is_file(), reason="script missing")
def test_stop_failure_while_healthy_rolls_back_tree_and_stamp(tmp_path: Path) -> None:
    """Stop fails, container stays healthy: copy last-healthy back, then the old stamp."""
    _seed_last_healthy_copy_set(tmp_path)
    stub = _stub_docker_dir(tmp_path, stop_fails=True, inspect_health="healthy")
    proc = _run_sync_script(tmp_path, stub)
    log = _docker_log(tmp_path)
    assert proc.returncode != 0, proc.stdout
    assert "MCP_SYNC_LAST_HEALTHY_TREE_ROLLBACK" in proc.stderr, proc.stderr
    _assert_tree_rolled_back(log, tmp_path)
    _assert_mktemp_removed(tmp_path)


@pytest.mark.skipif(not _SCRIPT.is_file(), reason="script missing")
def test_stop_failure_while_healthy_without_copy_set_marks_disagreement(
    tmp_path: Path,
) -> None:
    """No last-healthy copy set: leave the tree and say stamp and tree disagree."""
    stub = _stub_docker_dir(
        tmp_path,
        stop_fails=True,
        inspect_health="healthy",
        materialize_host_cp=False,
    )
    proc = _run_sync_script(tmp_path, stub)
    log = _docker_log(tmp_path)
    assert proc.returncode != 0, proc.stdout
    assert "STAMP_RESTORE" in log, log
    assert "oldsha" in (tmp_path / "container_stamp").read_text(encoding="utf-8")
    assert "MCP_SYNC_TREE_STAMP_DISAGREE" in proc.stderr, proc.stderr
    assert "MCP_SYNC_LAST_HEALTHY_TREE_ROLLBACK" not in proc.stderr
    assert "last-healthy/libs/." not in log


@pytest.mark.skipif(not _SCRIPT.is_file(), reason="script missing")
@pytest.mark.skipif(not _SCRIPT.is_file(), reason="script missing")
def test_rollback_services_cp_failure_restores_stamp_and_disagrees(
    tmp_path: Path,
) -> None:
    """A later last-healthy cp fails after libs succeeded: mixed tree, old stamp, disagree."""
    _seed_last_healthy_copy_set(tmp_path)
    stub = _stub_docker_dir(
        tmp_path,
        stop_fails=True,
        inspect_health="healthy",
        rollback_services_cp_fails=True,
    )
    proc = _run_sync_script(tmp_path, stub)
    log = _docker_log(tmp_path)
    assert "STAMP_RESTORE" in log, log
    assert "oldsha" in (tmp_path / "container_stamp").read_text(encoding="utf-8")
    assert "MCP_SYNC_TREE_STAMP_DISAGREE" in proc.stderr, proc.stderr
    assert "MCP_SYNC_LAST_HEALTHY_TREE_ROLLBACK" not in proc.stderr
    assert "last-healthy/libs/." in log, log
    assert "last-healthy/services/." in log, log


@pytest.mark.skipif(not _SCRIPT.is_file(), reason="script missing")
def test_stamp_restore_failure_after_tree_rollback_does_not_claim_success(
    tmp_path: Path,
) -> None:
    """Tree rollback succeeded but the stamp write failed: no success marker."""
    _seed_last_healthy_copy_set(tmp_path)
    stub = _stub_docker_dir(
        tmp_path,
        stop_fails=True,
        inspect_health="healthy",
        stamp_restore_fails=True,
    )
    proc = _run_sync_script(tmp_path, stub)
    assert "MCP_SYNC_LAST_HEALTHY_TREE_ROLLBACK" not in proc.stderr, proc.stderr
    assert "MCP_SYNC_TREE_STAMP_DISAGREE" in proc.stderr, proc.stderr


@pytest.mark.skipif(not _SCRIPT.is_file(), reason="script missing")
def test_snapshot_exec_failure_after_sync_does_not_claim_gate_restore(
    tmp_path: Path,
) -> None:
    """Snapshot exec fails after the tree sync: no gate-restore marker on a healthy container."""
    _seed_last_healthy_copy_set(tmp_path)
    stub = _stub_docker_dir(tmp_path, exec_true_fails=True, inspect_health="healthy")
    proc = _run_sync_script(tmp_path, stub)
    log = _docker_log(tmp_path)
    assert "MCP_SYNC_GATE_RESTORE" not in proc.stderr, proc.stderr
    assert "STAMP_WRITE" not in log, log
    assert "MCP_SYNC_LAST_HEALTHY_TREE_ROLLBACK" in proc.stderr, proc.stderr[-500:]
    _assert_mktemp_removed(tmp_path)


@pytest.mark.skipif(not _SCRIPT.is_file(), reason="script missing")
def test_sigterm_during_sync_cp_marks_tree_outcome(tmp_path: Path) -> None:
    """SIGTERM during source cp, before the stamp window, still settles the tree."""
    _seed_last_healthy_copy_set(tmp_path)
    stub = _stub_docker_dir(tmp_path, kill_on_sync_cp=True, inspect_health="healthy")
    proc = _run_sync_script(tmp_path, stub, timeout=90)
    assert proc.returncode != 0, proc.stdout
    assert "MCP_SYNC_LAST_HEALTHY_TREE_ROLLBACK" in proc.stderr, proc.stderr[-500:]
    assert "MCP_SYNC_GATE_RESTORE" not in proc.stderr


@pytest.mark.skipif(not _SCRIPT.is_file(), reason="script missing")
def test_sigpipe_between_snapshot_and_stamp_write_restores(tmp_path: Path) -> None:
    """SIGPIPE after the snapshot and before the stamp write restores and drops mktemp."""
    _seed_last_healthy_copy_set(tmp_path)
    stub = _stub_docker_dir(tmp_path, kill_before_stamp_write=True)
    proc = _run_sync_script(tmp_path, stub, timeout=90)
    log = _docker_log(tmp_path)
    assert "STAMP_WRITE" not in log, log
    assert "STAMP_RESTORE" in log, log
    assert "MCP_SYNC_LAST_HEALTHY_TREE_ROLLBACK" in proc.stderr, proc.stderr[-500:]
    _assert_mktemp_removed(tmp_path)


def _install_failing_tool(stub: Path, name: str) -> None:
    tool = stub / name
    tool.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
    tool.chmod(tool.stat().st_mode | stat.S_IXUSR)


@pytest.mark.skipif(not _SCRIPT.is_file(), reason="script missing")
def test_stamp_write_git_failure_restores(tmp_path: Path) -> None:
    """git failure inside the stamp write restores the snapshot and removes mktemp."""
    _seed_last_healthy_copy_set(tmp_path)
    stub = _stub_docker_dir(tmp_path)
    _install_failing_tool(stub, "git")
    proc = _run_sync_script(tmp_path, stub)
    log = _docker_log(tmp_path)
    assert proc.returncode != 0, proc.stdout
    assert "STAMP_RESTORE" in log, log
    assert "MCP_SYNC_LAST_HEALTHY_TREE_ROLLBACK" in proc.stderr, proc.stderr[-500:]
    _assert_mktemp_removed(tmp_path)


@pytest.mark.skipif(not _SCRIPT.is_file(), reason="script missing")
def test_stamp_write_sha256sum_failure_restores(tmp_path: Path) -> None:
    """sha256sum failure in the stamp write restores the snapshot and removes mktemp."""
    _seed_last_healthy_copy_set(tmp_path)
    stub = _stub_docker_dir(tmp_path)
    _install_failing_tool(stub, "sha256sum")
    proc = _run_sync_script(tmp_path, stub)
    log = _docker_log(tmp_path)
    assert proc.returncode != 0, proc.stdout
    assert "STAMP_RESTORE" in log, log
    _assert_mktemp_removed(tmp_path)


@pytest.mark.skipif(not _SCRIPT.is_file(), reason="script missing")
def test_stamp_write_chown_failure_restores(tmp_path: Path) -> None:
    """docker exec chown failure inside the stamp write restores and removes mktemp."""
    _seed_last_healthy_copy_set(tmp_path)
    stub = _stub_docker_dir(tmp_path, stamp_exec_fails=True)
    proc = _run_sync_script(tmp_path, stub)
    log = _docker_log(tmp_path)
    assert proc.returncode != 0, proc.stdout
    assert "STAMP_WRITE" not in log, log
    assert "STAMP_RESTORE" in log, log
    _assert_mktemp_removed(tmp_path)


def test_import_probe_refusal_skips_stamp_and_stop(tmp_path: Path) -> None:
    """AC5: import probe failure exits before cp/stop/stamp writes."""
    stub = _stub_docker_dir(
        tmp_path, inspect_health="healthy", import_probe_fails=True
    )
    proc = _run_sync_script(tmp_path, stub)
    log = _docker_log(tmp_path)
    assert proc.returncode == 1, proc.stdout
    assert "STAMP_WRITE" not in log, log
    assert "stop " not in log, log


@pytest.mark.skipif(not _SCRIPT.is_file(), reason="script missing")
def test_healthy_finish_no_trap_restore(tmp_path: Path) -> None:
    """Healthy restart exits 0 and does not rm the container.

    The stub's ``exec cat`` echoes the nonce written by ``exec sh -c printf``,
    so verify passes after health returns 0. A disarm that never ran would
    take the EXIT handler and rm.
    """
    stub = _stub_docker_dir(tmp_path, inspect_health="healthy", nonce_mode="stored")
    proc = _run_sync_script(tmp_path, stub)
    log = _docker_log(tmp_path)
    assert proc.returncode == 0, proc.stderr
    assert "Verified sync nonce survived restart" in proc.stdout
    assert "rm -f mcp-server" not in log


@pytest.mark.skipif(not _SCRIPT.is_file(), reason="script missing")
def test_verify_fail_after_healthy_does_not_restore(tmp_path: Path) -> None:
    """Health 0 then a bad nonce exits 1 with no restore argv.

    The EXIT trap is already disarmed, so a verify failure must not rm or
    compose-create the container.
    """
    stub = _stub_docker_dir(tmp_path, inspect_health="healthy", nonce_mode="wrong")
    proc = _run_sync_script(tmp_path, stub)
    log = _docker_log(tmp_path)
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "rm -f mcp-server" not in log
    assert "compose" not in log


def test_restore_handler_second_entry_is_noop(tmp_path: Path) -> None:
    """Script-level re-entry of the EXIT handler does not restore twice."""
    function = _extract_bash_function("_restore_window_exit")
    log = tmp_path / "restore.log"
    body = "\n".join(
        [
            "set +e",
            "shopt -s extdebug",
            "trap 'if [[ \"$BASH_COMMAND\" == exit\\ * ]]; then builtin return 0; fi' DEBUG",
            "CHECKED_IMAGE_ID=sha256:abc",
            "CHECKED_IMAGE_FILE=",
            "_RESTORE_HANDLER_RAN=0",
            "_STOP_SUCCEEDED=0",
            "_START_FAILED=0",
            "_STAMP_WRITTEN=0",
            "_SOURCE_SYNC_STAMP_SNAPSHOT=/nonexistent",
            "_cleanup_stamp_snapshot() { :; }",
            "gate() { printf '%s\\n' \"$*\" >> " + str(log) + "; }",
            "restore_source_sync_stamp_from_snapshot() { :; }",
            function,
            "_restore_window_exit",
            "_restore_window_exit",
            "",
        ]
    )
    proc = subprocess.run(
        ["bash", "-c", body],
        capture_output=True,
        text=True,
        check=False,
    )
    recorded = log.read_text(encoding="utf-8") if log.is_file() else ""
    assert proc.returncode == 0, proc.stderr
    assert recorded.count("restore") == 1, recorded


def _extract_bash_function(name: str) -> str:
    text = _SCRIPT.read_text(encoding="utf-8")
    marker = f"{name}() {{"
    start = text.index(marker)
    depth = 0
    for index in range(start, len(text)):
        if text[index] == "{":
            depth += 1
        elif text[index] == "}":
            depth -= 1
            if depth == 0:
                return text[start : index + 1]
    raise AssertionError(f"{name} not closed in {_SCRIPT}")


def test_trap_handler_pipe_trap_allows_restore_after_failed_echo() -> None:
    """Binding (a): the script EXIT handler ignores a real broken-pipe echo."""
    function = _extract_bash_function("_restore_window_exit")
    body = "\n".join(
        [
            "set -euo pipefail",
            'exec 3>&"$PIPE_FD"',
            "CHECKED_IMAGE_ID=sha256:abc",
            "CHECKED_IMAGE_FILE=",
            "_RESTORE_HANDLER_RAN=0",
            "_STOP_SUCCEEDED=0",
            "_START_FAILED=0",
            "_STAMP_WRITTEN=0",
            "_SOURCE_SYNC_STAMP_SNAPSHOT=/nonexistent",
            "_cleanup_stamp_snapshot() { :; }",
            "restore_source_sync_stamp_from_snapshot() { :; }",
            "gate() {",
            "  echo restore-byte >&3",
            "  echo RESTORE_RAN",
            "}",
            function,
            "trap _restore_window_exit EXIT",
            "false",
            "",
        ]
    )
    read_fd, write_fd = os.pipe()
    os.close(read_fd)
    os.set_inheritable(write_fd, True)
    env = os.environ.copy()
    env["PIPE_FD"] = str(write_fd)
    proc = subprocess.run(
        ["bash", "-c", body],
        pass_fds=(write_fd,),
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    os.close(write_fd)
    assert "Broken pipe" in proc.stderr
    assert "RESTORE_RAN" in proc.stdout
    assert proc.returncode == 1


def test_probe_pinned_before_container_create_path(tmp_path: Path) -> None:
    """Binding (b): kill the script after the snapshot and before compose create."""
    stub = _stub_docker_dir(
        tmp_path,
        container_exists=False,
        inspect_health="healthy",
        kill_on_run=True,
    )
    env = _minimal_env(tmp_path, stub)
    env["PYTHON"] = os.environ.get(
        "PYTHON", str(Path.home() / ".venvs/universal/bin/python")
    )
    env["ULG_REPO"] = str(_REPO_ROOT)
    env["PROJECT_ROOT"] = str(_REPO_ROOT)
    proc = subprocess.run(
        ["bash", str(_SCRIPT)],
        cwd=str(_REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
        timeout=90,
        check=False,
    )
    log = (
        (tmp_path / "docker.log").read_text(encoding="utf-8")
        if (tmp_path / "docker.log").is_file()
        else ""
    )
    cp_idx = log.find("cp ")
    run_idx = log.find("run ")
    assert cp_idx >= 0, log
    assert run_idx >= 0, log
    assert run_idx > cp_idx, log
    assert proc.returncode == -signal.SIGTERM, (proc.returncode, proc.stderr[-400:])
    assert "compose" not in log, log


def test_nocache_path_writes_stamp_before_stop_start(tmp_path: Path) -> None:
    """AC6: --no-cache path writes stamp then docker stop/start before healthy."""
    stub = _stub_docker_dir(tmp_path, inspect_health="healthy", nonce_mode="stored")
    bash_log = tmp_path / "bash.log"
    stub_bash = stub / "bash"
    stub_bash.write_text(
        "#!/bin/bash\n"
        'if [[ "$1" == *build-mcp.sh* || "$2" == *build-mcp.sh* ]]; then\n'
        "  exit 0\n"
        "fi\n"
        'exec /bin/bash "$@"\n'
    )
    stub_bash.chmod(stub_bash.stat().st_mode | stat.S_IXUSR)
    env = _minimal_env(tmp_path, stub)
    env["PYTHON"] = _python()
    env["ULG_REPO"] = str(_REPO_ROOT)
    env["PROJECT_ROOT"] = str(_REPO_ROOT)
    env["PATH"] = f"{stub}:{env.get('PATH', '')}"
    proc = subprocess.run(
        ["bash", str(_SCRIPT), "--no-cache"],
        cwd=str(_REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    log = _docker_log(tmp_path)
    idx = _log_indices(log)
    assert proc.returncode == 0, proc.stderr + proc.stdout[-500:]
    assert log.count("STAMP_WRITE") == 1, log
    assert idx["stamp_write"] < idx["stop"] < idx["start"], log


def test_nocache_unhealthy_does_not_start_build(tmp_path: Path) -> None:
    """S2(5): --no-cache does not start the build when mcp-server is unhealthy."""
    stub = _stub_docker_dir(tmp_path, inspect_health="unhealthy", container_exists=True)
    bash_log = tmp_path / "bash.log"
    stub_bash = stub / "bash"
    stub_bash.write_text(
        "#!/bin/bash\n"
        'if [[ "$1" == *build-mcp.sh* || "$2" == *build-mcp.sh* ]]; then\n'
        f'  echo "BUILD $*" >> {bash_log}\n'
        "  exit 0\n"
        "fi\n"
        'exec /bin/bash "$@"\n'
    )
    stub_bash.chmod(stub_bash.stat().st_mode | stat.S_IXUSR)
    env = _minimal_env(tmp_path, stub)
    env["PYTHON"] = str(Path.home() / ".venvs/universal/bin/python")
    env["ULG_REPO"] = str(_REPO_ROOT)
    env["PROJECT_ROOT"] = str(_REPO_ROOT)
    env["PATH"] = f"{stub}:{env.get('PATH', '')}"
    proc = subprocess.run(
        ["bash", str(_SCRIPT), "--no-cache"],
        cwd=str(_REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
        timeout=90,
        check=False,
    )
    recorded = bash_log.read_text(encoding="utf-8") if bash_log.is_file() else ""
    assert "build-mcp.sh" not in recorded
    assert proc.returncode != 0
