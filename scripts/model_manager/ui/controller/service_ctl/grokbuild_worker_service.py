"""grokbuild-worker service lifecycle — start, stop, PID/port management.

Mirrors the cortex-api / agent-bus pattern but runs on TCP (default
127.0.0.1:8090) instead of UDS — Stargate's proxy router talks to the
worker over HTTP, and the FastAPI contract is identical across the
bare-metal-systemd and container deploy shapes.

No ``GROKBUILD_AUTH_TOKEN`` is set or forwarded: auth is Stargate
pass-through (operator override, see Phase A.2 plan).
"""

from __future__ import annotations

import os
from collections.abc import Awaitable, Callable
from pathlib import Path

from ...model.service_state import ServiceState
from ..service_config import GATEWAY_DIR
from .uvicorn_service import _start_uvicorn_service, _stop_uvicorn_service

_APP_MODULE = "services.grokbuild_worker.app:app"
_PID_FILE = GATEWAY_DIR / "grokbuild-worker.pid"
_LOCK_FILE = GATEWAY_DIR / "grokbuild-worker.lock"
_LOG_DIR = Path("/tmp/logs/grokbuild-worker")
_DEFAULT_HOST = os.environ.get("GROKBUILD_WORKER_HOST", "127.0.0.1")
_DEFAULT_PORT = int(os.environ.get("GROKBUILD_WORKER_PORT", "8090"))

# XDG-compliant defaults (user-writable, no root required).  The lib
# (`libs/grokbuild/constants.py`) and worker config both use the same tilde
# string; Path.expanduser() is applied downstream (in config._env_path and
# constants._SIDECAR_DIR).  Propagate unconditionally so the lib's default
# never diverges from the worker's view when these env vars are absent.
_DEFAULT_SIDECAR_DIR = "~/.local/share/grokbuild-worker/sidecars"
_DEFAULT_REGISTRY_PATH = "~/.local/share/grokbuild-worker/registry.json"


def _tcp_config() -> tuple[str, int]:
    return (_DEFAULT_HOST, _DEFAULT_PORT)


def _runtime_env() -> dict[str, str]:
    """Build runtime env exposing config knobs to the worker process.

    ``GROKBUILD_SIDECAR_DIR`` and ``GROKBUILD_REGISTRY_PATH`` are set
    unconditionally to the operator-locked defaults; otherwise the lib's
    constants.py / registry.py defaults would diverge from the worker's
    ``WorkerConfig`` defaults when these env vars are missing in the
    parent shell.
    """
    env: dict[str, str] = {
        "GROKBUILD_WORKER_HOST": _DEFAULT_HOST,
        "GROKBUILD_WORKER_PORT": str(_DEFAULT_PORT),
        "GROKBUILD_SIDECAR_DIR": os.environ.get(
            "GROKBUILD_SIDECAR_DIR", _DEFAULT_SIDECAR_DIR
        ),
        "GROKBUILD_REGISTRY_PATH": os.environ.get(
            "GROKBUILD_REGISTRY_PATH", _DEFAULT_REGISTRY_PATH
        ),
    }
    for key in (
        "GROK_BIN_PATH",
        "GROK_AUTH_DIR",
        "PROJECTS_ROOT",
    ):
        value = os.environ.get(key)
        if value:
            env[key] = value
    return env


async def start_grokbuild_worker(
    service_state: ServiceState,
    root: Path,
    kill_and_wait: Callable[..., Awaitable[str]],  # noqa: ARG001
) -> str:
    """Start grokbuild-worker as a host uvicorn process on TCP."""
    return await _start_uvicorn_service(
        service_state=service_state,
        root=root,
        app_module=_APP_MODULE,
        pid_file=_PID_FILE,
        lock_file=_LOCK_FILE,
        service_name="grokbuild-worker",
        socket_path=None,
        tcp_config=_tcp_config(),
        log_dir=_LOG_DIR,
        log_filename="grokbuild-worker.log",
        extra_env=_runtime_env(),
    )


async def stop_grokbuild_worker(
    service_state: ServiceState,
    root: Path,  # noqa: ARG001
    kill_and_wait: Callable[..., Awaitable[str]],
) -> str:
    """Stop grokbuild-worker gracefully."""
    return await _stop_uvicorn_service(
        service_state=service_state,
        kill_and_wait=kill_and_wait,
        pid_file=_PID_FILE,
        lock_file=_LOCK_FILE,
        service_name="grokbuild-worker",
        socket_path=None,
        tcp_config=_tcp_config(),
        app_module=_APP_MODULE,
    )
