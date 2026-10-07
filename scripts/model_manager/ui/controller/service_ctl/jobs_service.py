"""Jobs satellite lifecycle — uvicorn on the jobs UDS.

Mirrors the agent-bus host process: one socket, a PID file under the gateway
directory, and tokens copied from the environment the manage process already
holds. This module does not open the journal.
"""

from __future__ import annotations

import os
from collections.abc import Awaitable, Callable
from pathlib import Path

from ...model.service_state import ServiceState
from ..service_config import GATEWAY_DIR, load_mcp_config
from .uvicorn_service import _start_uvicorn_service, _stop_uvicorn_service

_JOBS_APP_MODULE = "jobs.server:app"
_JOBS_PID_FILE = GATEWAY_DIR / "jobs.pid"
_JOBS_LOCK_FILE = GATEWAY_DIR / "jobs.lock"
_JOBS_SOCKET = Path(os.environ.get("JOBS_SOCK", "/tmp/universal-protocol/jobs.sock"))
_JOBS_LOG_DIR = Path("/tmp/logs/jobs")


def _jobs_runtime_env() -> dict[str, str]:
    """Copy bus and jobs tokens into the child when manage already has them."""
    env = {"JOBS_SOCK": str(_JOBS_SOCKET)}
    mcp_cfg = load_mcp_config()
    if mcp_cfg is not None and mcp_cfg.agent_bus_token:
        env["AGENT_BUS_TOKEN"] = mcp_cfg.agent_bus_token
    jobs_token = os.environ.get("JOBS_TOKEN", "")
    if jobs_token:
        env["JOBS_TOKEN"] = jobs_token
    return env


async def start_jobs(
    service_state: ServiceState,
    root: Path,
    kill_and_wait: Callable[..., Awaitable[str]],  # noqa: ARG001
) -> str:
    """Start the jobs satellite as a host uvicorn process on its UDS."""
    return await _start_uvicorn_service(
        service_state=service_state,
        root=root,
        app_module=_JOBS_APP_MODULE,
        pid_file=_JOBS_PID_FILE,
        lock_file=_JOBS_LOCK_FILE,
        service_name="Jobs",
        scope_name="jobs",
        socket_path=_JOBS_SOCKET,
        tcp_config=None,
        log_dir=_JOBS_LOG_DIR,
        log_filename="jobs.log",
        extra_env=_jobs_runtime_env(),
    )


async def stop_jobs(
    service_state: ServiceState,
    root: Path,  # noqa: ARG001
    kill_and_wait: Callable[..., Awaitable[str]],
) -> str:
    """Stop the jobs satellite process without deleting its journal database."""
    return await _stop_uvicorn_service(
        service_state=service_state,
        kill_and_wait=kill_and_wait,
        pid_file=_JOBS_PID_FILE,
        lock_file=_JOBS_LOCK_FILE,
        service_name="Jobs",
        socket_path=_JOBS_SOCKET,
        tcp_config=None,
        app_module=_JOBS_APP_MODULE,
    )
