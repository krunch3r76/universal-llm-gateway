"""git-integration-worker service lifecycle — start, stop, PID/port management.

Host uvicorn on TCP (default 127.0.0.1:8091).
Stargate's git proxy router talks to the worker over HTTP.
"""

from __future__ import annotations

import os
from collections.abc import Awaitable, Callable
from pathlib import Path

from ...model.service_state import ServiceState
from ..service_config import GATEWAY_DIR
from .uvicorn_service import _start_uvicorn_service, _stop_uvicorn_service

_APP_MODULE = "services.git_integration_worker.app:app"
_PID_FILE = GATEWAY_DIR / "git-integration-worker.pid"
_LOCK_FILE = GATEWAY_DIR / "git-integration-worker.lock"
_LOG_DIR = Path("/tmp/logs/git-integration-worker")
_DEFAULT_HOST = os.environ.get("GIT_INTEGRATION_WORKER_HOST", "127.0.0.1")
_DEFAULT_PORT = int(os.environ.get("GIT_INTEGRATION_WORKER_PORT", "8091"))
_DEFAULT_SOURCE_REPO = "/mnt/torus/projects/universal-llm-gateway"
_DEFAULT_WORKTREE_ROOT = "/mnt/torus/projects/ulg-arc-worktrees"


def _tcp_config() -> tuple[str, int]:
    return (_DEFAULT_HOST, _DEFAULT_PORT)


def _expanded_env_path(key: str, default: str) -> str:
    return str(Path(os.environ.get(key, default)).expanduser())


def _path_with_venv_first(path: str, venv_bin: str) -> str:
    """Return PATH with *venv_bin* first, even when it already appears later.

    Presence-only prepend left ``~/.local/bin`` winning when both were on PATH
    (arc 7190: GIW closeout lint resolved ruff 0.12.2 instead of the venv pin).
    """
    parts = [p for p in path.split(":") if p and p != venv_bin]
    return f"{venv_bin}:{':'.join(parts)}" if parts else venv_bin


def _runtime_env() -> dict[str, str]:
    """Env overlay for the GIW uvicorn subprocess, including PATH.

    ``PATH`` always puts ``~/.venvs/universal/bin`` first so ruff/pytest
    resolve to the repo pin even when ``~/.local/bin`` is already present.
    """
    repo = _expanded_env_path("GIT_INTEGRATION_SOURCE_REPO", _DEFAULT_SOURCE_REPO)
    stargate = str(Path(repo) / "services" / "universal-stargate")
    libs = str(Path(repo) / "libs")
    # nested_outcome → systems.frontier_consult (lives under stargate).
    # extra_env replaces uvicorn_service's libs-only PYTHONPATH, so include both.
    env: dict[str, str] = {
        "PYTHONPATH": f"{stargate}:{libs}",
        "GIT_INTEGRATION_WORKER_HOST": _DEFAULT_HOST,
        "GIT_INTEGRATION_WORKER_PORT": str(_DEFAULT_PORT),
        "GIT_INTEGRATION_SOURCE_REPO": repo,
        "GIT_INTEGRATION_WORKTREE_ROOT": _expanded_env_path(
            "GIT_INTEGRATION_WORKTREE_ROOT", _DEFAULT_WORKTREE_ROOT
        ),
    }
    green_gate = os.environ.get("GIT_INTEGRATION_GREEN_GATE_CMD")
    if green_gate:
        env["GIT_INTEGRATION_GREEN_GATE_CMD"] = green_gate
    # Always prepend venv bin so it wins over ~/.local/bin, not only when
    # absent. Every GIW-spawned tool (ruff, pytest, fastmcp-remote) inherits.
    universal_venv = Path.home() / ".venvs" / "universal"
    venv_bin = str(universal_venv / "bin")
    path = os.environ.get("PATH", "/usr/bin:/bin")
    env["PATH"] = _path_with_venv_first(path, venv_bin)
    env["VIRTUAL_ENV"] = str(universal_venv)
    # This setting is no longer an override surface; remove a stale parent
    # value so the worker cannot inherit a non-universal dispatch venv.
    env.pop("CURSOR_SDK_VENV_PATH", None)
    return env


async def start_git_integration_worker(
    service_state: ServiceState,
    root: Path,
    kill_and_wait: Callable[..., Awaitable[str]],  # noqa: ARG001
) -> str:
    """Start git-integration-worker as a host uvicorn process on TCP."""
    return await _start_uvicorn_service(
        service_state=service_state,
        root=root,
        app_module=_APP_MODULE,
        pid_file=_PID_FILE,
        lock_file=_LOCK_FILE,
        service_name="git-integration-worker",
        socket_path=None,
        tcp_config=_tcp_config(),
        log_dir=_LOG_DIR,
        log_filename="git-integration-worker.log",
        extra_env=_runtime_env(),
    )


async def stop_git_integration_worker(
    service_state: ServiceState,
    root: Path,  # noqa: ARG001
    kill_and_wait: Callable[..., Awaitable[str]],
) -> str:
    """Stop git-integration-worker gracefully."""
    return await _stop_uvicorn_service(
        service_state=service_state,
        kill_and_wait=kill_and_wait,
        pid_file=_PID_FILE,
        lock_file=_LOCK_FILE,
        service_name="git-integration-worker",
        socket_path=None,
        tcp_config=_tcp_config(),
        app_module=_APP_MODULE,
    )
