"""Shared uvicorn service start/stop logic for RAG and Cloud Proxy.

Part of the model_manager UI controller. Extracts common pre-launch checks,
environment setup, subprocess creation, and stop critical-section logic.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from pathlib import Path

from ...model.service_state import ServiceState
from ..service_config import GATEWAY_DIR, build_service_env, ensure_socket_dir
from .utils import (
    _acquire_lock,
    _find_uvicorn_pid_by_cmdline,
    _release_lock,
    _safe_unlink_stale_socket,
    _validate_uvicorn_pid,
)

logger = logging.getLogger(__name__)

# Per-service asyncio locks keyed by app_module.
# ∀ concurrent start_rag() calls: serialized within the same process.
# The file lock in _pre_launch() guards cross-process safety; this covers
# the intra-process race between _pre_launch() and PID file registration
# (a 3-second window where concurrent calls both find no valid PID).
_service_start_locks: dict[str, asyncio.Lock] = {}


def _get_service_start_lock(app_module: str) -> asyncio.Lock:
    """Return the per-app startup lock used to serialize local start attempts.

    The lock complements the cross-process file lock in `_pre_launch()` by
    preventing two in-process callers from both passing the pre-launch checks
    before the PID file for the new child process has been written.
    """
    if app_module not in _service_start_locks:
        _service_start_locks[app_module] = asyncio.Lock()
    return _service_start_locks[app_module]


def _read_log_tail(log_file: Path, max_chars: int = 1000) -> str:
    """Read at most the last ``max_chars`` from a service log file."""
    with log_file.open("rb") as fh:
        fh.seek(0, 2)
        size = fh.tell()
        seek = max(0, size - max_chars * 4)
        fh.seek(seek)
        data = fh.read()
    return data.decode("utf-8", errors="replace")[-max_chars:]


def _cleanup_uds_socket(lock_file: Path, socket_path: Path | None) -> None:
    """Best-effort cleanup for stale UDS socket files."""
    if socket_path is None or not socket_path.exists():
        return
    fd = _acquire_lock(lock_file)
    try:
        _safe_unlink_stale_socket(socket_path)
    finally:
        _release_lock(fd)


async def _start_uvicorn_service(
    service_state: ServiceState,
    root: Path,
    app_module: str,
    pid_file: Path,
    lock_file: Path,
    service_name: str,
    socket_path: Path | None,
    tcp_config: tuple[str, int] | None,
    log_dir: Path,
    log_filename: str,
    extra_env: dict[str, str] | None = None,
    on_timeout_success: (
        Callable[[Path | None, tuple[str, int] | None, int], None] | None
    ) = None,
) -> str:
    """Start a uvicorn-based service. Returns status message."""
    uds_mode = tcp_config is None
    if uds_mode and socket_path is None:
        return f"{service_name} configuration error: UDS mode requires socket_path."

    def _pre_launch() -> str | None:
        fd = _acquire_lock(lock_file)
        try:
            recorded_pid, _ = service_state._resolve_pid_file(pid_file)
            if recorded_pid is not None and _validate_uvicorn_pid(
                recorded_pid, app_module, uds_mode=uds_mode
            ):
                return f"{service_name} is already running."
            if pid_file.exists():
                pid_file.unlink(missing_ok=True)
            if uds_mode and socket_path is not None:
                err = ensure_socket_dir()
                if err:
                    return err
                if socket_path.exists():
                    if not _safe_unlink_stale_socket(socket_path):
                        return f"{service_name} is already running (socket in use)."
            return None
        finally:
            _release_lock(fd)

    async with _get_service_start_lock(app_module):
        loop = asyncio.get_running_loop()
        pre_result = await loop.run_in_executor(None, _pre_launch)
        if pre_result is not None:
            return pre_result

        venv_python = Path.home() / ".venvs" / "universal" / "bin" / "python"
        python = str(venv_python) if venv_python.exists() else "python3"
        log_dir.mkdir(parents=True, exist_ok=True)
        log_file = log_dir / log_filename
        env = build_service_env(root)
        libs_path = str(root / "libs")
        existing_pythonpath = env.get("PYTHONPATH", "")
        env["PYTHONPATH"] = (
            f"{libs_path}:{existing_pythonpath}" if existing_pythonpath else libs_path
        )
        if extra_env:
            env.update(extra_env)

        uvicorn_args: list[str] = ["-m", "uvicorn", app_module]
        if uds_mode and socket_path is not None:
            uvicorn_args.extend(["--uds", str(socket_path)])
        elif tcp_config is not None:
            host, port = tcp_config
            uvicorn_args.extend(["--host", host, "--port", str(port)])

        with log_file.open("w") as log_fh:
            process = await asyncio.create_subprocess_exec(
                python,
                *uvicorn_args,
                env=env,
                cwd=str(root),
                stdout=log_fh,
                stderr=asyncio.subprocess.STDOUT,
                start_new_session=True,
            )

        try:
            exit_code = await asyncio.wait_for(process.wait(), timeout=3.0)
            tail = _read_log_tail(log_file)
            return f"{service_name} failed (exit {exit_code}).\n{tail}"
        except TimeoutError:
            logger.info(
                "%s subprocess remained alive after startup probe", service_name
            )
            if on_timeout_success is not None:
                on_timeout_success(socket_path, tcp_config, process.pid)
            fd = _acquire_lock(lock_file)
            try:
                GATEWAY_DIR.mkdir(parents=True, exist_ok=True)
                pid_file.write_text(str(process.pid) + "\n")
            finally:
                _release_lock(fd)
            msg = f"{service_name} starting (PID {process.pid})."
            if not uds_mode and tcp_config is not None:
                msg += f" on {tcp_config[0]}:{tcp_config[1]}"
            return msg


async def _stop_uvicorn_service(
    service_state: ServiceState,
    kill_and_wait: Callable[..., Awaitable[str]],
    pid_file: Path,
    lock_file: Path,
    service_name: str,
    socket_path: Path | None,
    tcp_config: tuple[str, int] | None,
    app_module: str,
) -> str:
    """Stop a uvicorn-based service. Returns status message."""
    uds_mode = tcp_config is None

    def _stop_critical_section() -> tuple[int | None, bool, str | None]:
        fd = _acquire_lock(lock_file)
        try:
            recorded_pid, _ = service_state._resolve_pid_file(pid_file)
            if recorded_pid is not None and _validate_uvicorn_pid(
                recorded_pid, app_module, uds_mode=uds_mode
            ):
                return recorded_pid, True, None
            if recorded_pid is not None and _validate_uvicorn_pid(
                recorded_pid, app_module, uds_mode=not uds_mode
            ):
                logger.warning(
                    "%s PID %d is running in %s mode but config now wants %s "
                    "mode — stopping stale process.",
                    service_name,
                    recorded_pid,
                    "UDS" if uds_mode else "TCP",
                    "TCP" if uds_mode else "UDS",
                )
                return recorded_pid, True, None
            pid_file.unlink(missing_ok=True)
            if uds_mode:
                fallback_pid = _find_uvicorn_pid_by_cmdline(app_module, uds_mode=True)
                if fallback_pid is not None:
                    return fallback_pid, False, None
                return None, False, None
            if tcp_config is not None:
                port = tcp_config[1]
                if not service_state._port_open(port):
                    return None, False, None
                listener = service_state._find_listener_pid(port)
                if listener is None:
                    return (
                        None,
                        False,
                        (
                            f"Port {port} is open but the listener could not be "
                            f"identified.\nRun: ss -tlnp 'sport = :{port}'"
                        ),
                    )
                return listener, False, None
            return None, False, None
        finally:
            _release_lock(fd)

    loop = asyncio.get_running_loop()
    target_pid, had_pid_file, err_msg = await loop.run_in_executor(
        None, _stop_critical_section
    )

    if err_msg is not None:
        return err_msg
    if target_pid is None:
        if uds_mode:
            _cleanup_uds_socket(lock_file, socket_path)
        return f"{service_name} is not running."

    result = await kill_and_wait(
        target_pid,
        pid_file if had_pid_file else None,
        service_name=service_name,
    )

    if uds_mode:
        _cleanup_uds_socket(lock_file, socket_path)

    return result
