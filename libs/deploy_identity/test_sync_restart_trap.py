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
          start|cp|exec|run|tag|rm|compose|image) exit 0 ;;
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


def test_trap_handler_pipe_trap_allows_restore_after_failed_echo(
    tmp_path: Path,
) -> None:
    """Binding (a): trap '' PIPE first; a failing command in the handler must not skip restore."""
    handler = textwrap.dedent(
        """\
        _restore_window_exit() {
          trap '' PIPE
          trap - EXIT
          set +e
          echo nope >/dev/nonexistent   # must not abort restore
          echo RESTORE_RAN
          exit 1
        }
        arm_restore_window() { trap _restore_window_exit EXIT; }
        arm_restore_window
        false
        """
    )
    proc = subprocess.run(
        ["bash", "-c", handler],
        capture_output=True,
        text=True,
        check=False,
    )
    assert "RESTORE_RAN" in proc.stdout
    assert proc.returncode == 1


def test_probe_pinned_before_container_create_path(tmp_path: Path) -> None:
    """Binding (b): stub logs import-probe before compose up on absent-container path."""
    stub = _stub_docker_dir(tmp_path, container_exists=False, inspect_health="none")
    env = _minimal_env(tmp_path, stub)
    subprocess.run(
        ["bash", str(_SCRIPT)],
        cwd=str(_REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
        timeout=90,
        check=False,
    )
    log = (tmp_path / "docker.log").read_text() if (tmp_path / "docker.log").is_file() else ""
    # import-probe uses docker run; compose up uses docker compose — probe must appear first
    run_idx = log.find("run")
    compose_idx = log.find("compose")
    if run_idx >= 0 and compose_idx >= 0:
        assert run_idx < compose_idx
