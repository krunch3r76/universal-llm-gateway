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
    inspect_health: str = "healthy",
    container_exists: bool = True,
    kill_on_run: bool = False,
    nonce_mode: str = "stored",
) -> Path:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "docker.log"
    nonce_file = tmp_path / "nonce"
    script = textwrap.dedent(
        f"""\
        #!/usr/bin/env bash
        echo "$@" >> {log}
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
              echo "{inspect_health}"
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
            if [[ "{str(stop_fails).lower()}" == "true" ]]; then
              exit 1
            fi
            exit 0
            ;;
          run)
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
          start|cp|tag|rm|compose|image) exit 0 ;;
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
            "gate() { printf '%s\\n' \"$*\" >> " + str(log) + "; }",
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
