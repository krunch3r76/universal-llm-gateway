"""cdp-ask local lifecycle — host uvicorn via scripts/cdp-ask on TCP :8770."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from pathlib import Path

from ...model.service_state import ServiceState
from ..service_config import (
    GATEWAY_DIR,
    apply_host_service_logging_env,
    build_service_env,
    cdp_ask_url_config,
)
from .host_spawn import await_popen_started, spawn_detached_host_process
from .startup_probe import StartupOutcome
from .utils import _acquire_lock, _release_lock

logger = logging.getLogger(__name__)

_PID_FILE = GATEWAY_DIR / "cdp-ask.pid"
_LOCK_FILE = GATEWAY_DIR / "cdp-ask.lock"
_LOG_DIR = Path("/tmp/logs/cdp-ask")
_SERVICE_NAME = "cdp-ask"


def _bind_config() -> tuple[str, int] | None:
    cfg = cdp_ask_url_config()
    if cfg is None:
        return None
    host, port, _base = cfg
    bind_host = "127.0.0.1" if host in {"localhost", "127.0.0.1", "::1"} else "0.0.0.0"
    return bind_host, port


async def start_cdp_ask(
    service_state: ServiceState,
    root: Path,
    kill_and_wait: Callable[..., Awaitable[str]],  # noqa: ARG001
) -> str:
    """Start cdp-ask as a host subprocess via ``scripts/cdp-ask``."""
    tcp = _bind_config()
    if tcp is None:
        return f"{_SERVICE_NAME} configuration error: PROJECT_ASK_URL unset."
    host, port = tcp
    script = root / "scripts" / "cdp-ask"
    if not script.exists():
        return f"Script not found: {script}"

    def _pre_launch() -> str | None:
        fd = _acquire_lock(_LOCK_FILE)
        try:
            recorded_pid, _ = service_state._resolve_pid_file(_PID_FILE)
            if recorded_pid is not None and service_state._pid_alive(recorded_pid):
                if service_state._port_open(port, host if host != "0.0.0.0" else "127.0.0.1"):
                    return f"{_SERVICE_NAME} is already running."
            if _PID_FILE.exists():
                _PID_FILE.unlink(missing_ok=True)
            return None
        finally:
            _release_lock(fd)

    loop = asyncio.get_running_loop()
    pre_result = await loop.run_in_executor(None, _pre_launch)
    if pre_result is not None:
        return pre_result

    _LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_file = _LOG_DIR / "cdp-ask.log"
    env = build_service_env(root)
    apply_host_service_logging_env(
        env, log_dir=_LOG_DIR, log_filename="cdp-ask.log"
    )
    libs_path = str(root / "libs")
    existing_pythonpath = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = (
        f"{libs_path}:{existing_pythonpath}" if existing_pythonpath else libs_path
    )

    venv_python = Path.home() / ".venvs" / "universal" / "bin" / "python"
    python = str(venv_python) if venv_python.exists() else "python3"

    process = spawn_detached_host_process(
        [python, str(script), "--host", host, "--port", str(port)],
        cwd=root,
        env=env,
        log_file=log_file,
    )

    probe_host = "127.0.0.1" if host == "0.0.0.0" else host

    def _ready() -> bool:
        return service_state._port_open(port, probe_host)

    outcome, exit_code = await await_popen_started(process, ready=_ready)
    if outcome is StartupOutcome.CRASHED:
        tail = log_file.read_text(errors="replace")[-1000:]
        return f"{_SERVICE_NAME} failed (exit {exit_code}).\n{tail}"

    fd = _acquire_lock(_LOCK_FILE)
    try:
        GATEWAY_DIR.mkdir(parents=True, exist_ok=True)
        _PID_FILE.write_text(f"{process.pid}\n")
    finally:
        _release_lock(fd)
    return f"{_SERVICE_NAME} starting (PID {process.pid}) on {host}:{port}."


async def stop_cdp_ask(
    service_state: ServiceState,
    root: Path,  # noqa: ARG001
    kill_and_wait: Callable[..., Awaitable[str]],
) -> str:
    """Stop cdp-ask gracefully."""
    tcp = _bind_config()
    port = tcp[1] if tcp is not None else 8770

    def _stop_critical_section() -> tuple[int | None, bool, str | None]:
        fd = _acquire_lock(_LOCK_FILE)
        try:
            recorded_pid, _ = service_state._resolve_pid_file(_PID_FILE)
            if recorded_pid is not None and service_state._pid_alive(recorded_pid):
                return recorded_pid, True, None
            _PID_FILE.unlink(missing_ok=True)
            listener = service_state._find_listener_pid(port)
            if listener is None:
                return None, False, None
            return listener, False, None
        finally:
            _release_lock(fd)

    loop = asyncio.get_running_loop()
    target_pid, had_pid_file, _ = await loop.run_in_executor(None, _stop_critical_section)
    if target_pid is None:
        return f"{_SERVICE_NAME} is not running."
    return await kill_and_wait(
        target_pid,
        _PID_FILE if had_pid_file else None,
        service_name=_SERVICE_NAME,
    )
