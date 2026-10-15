"""Trap integration tests for scripts/sync-and-restart-mcp.sh (stub docker on PATH)."""

from __future__ import annotations

import os
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
) -> Path:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "docker.log"
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
          start|cp|exec|tag|rm|compose|image) exit 0 ;;
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


@pytest.mark.skipif(not _SCRIPT.is_file(), reason="script missing")
def test_healthy_finish_no_trap_restore(tmp_path: Path) -> None:
    stub = _stub_docker_dir(tmp_path, inspect_health="healthy")
    env = _minimal_env(tmp_path, stub)
    # Fake workspace layout the script expects
    ws = _REPO_ROOT
    subprocess.run(
        ["bash", str(_SCRIPT)],
        cwd=str(ws),
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    log = tmp_path / "docker.log"
    if log.is_file():
        text = log.read_text()
        assert "rm" not in text or "-f" not in text.split("rm", 1)[-1][:20]


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
    compose_idx = log.find("compose")
    assert cp_idx >= 0, log
    assert run_idx >= 0, log
    assert run_idx > cp_idx, log
    assert compose_idx == -1 or run_idx < compose_idx, log
    assert proc.returncode != 0


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
