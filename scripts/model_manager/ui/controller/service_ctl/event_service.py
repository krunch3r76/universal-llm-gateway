"""Event service lifecycle — start, stop as host subprocess.

The event service uses ``python -m event_store serve`` (not bare uvicorn)
because it manages dual sockets: NDJSON ingest + FastAPI/uvicorn query.
"""

from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import Awaitable, Callable
from pathlib import Path

from ...model.service_state import ServiceState
from ..service_config import (
    GATEWAY_DIR,
    apply_host_service_logging_env,
    build_service_env,
    ensure_event_service_config,
    ensure_socket_dir,
    load_event_service_config,
)
from .utils import (
    _acquire_lock,
    _find_module_pid_by_cmdline,
    _release_lock,
    _safe_unlink_stale_socket,
    _validate_module_pid,
)

logger = logging.getLogger(__name__)

_MODULE_NAME = "event_store"
_PID_FILE = GATEWAY_DIR / "event-service.pid"
_LOCK_FILE = GATEWAY_DIR / "event-service.lock"
_LOG_DIR = Path("/tmp/logs/event-service")
_LOG_FILENAME = "event-service.log"
_SERVICE_NAME = "Event service"

_DEFAULT_DB = "~/.events/events.db"
_DEFAULT_INGEST_SOCK = os.environ.get(
    "EVENTS_INGEST_SOCK", "/tmp/universal-protocol/events.sock"
)
_DEFAULT_QUERY_SOCK = os.environ.get(
    "EVENTS_QUERY_SOCK", "/tmp/universal-protocol/events-query.sock"
)

_start_lock: asyncio.Lock | None = None


def _get_start_lock() -> asyncio.Lock:
    global _start_lock
    if _start_lock is None:
        _start_lock = asyncio.Lock()
    return _start_lock


def _read_log_tail(log_file: Path, max_chars: int = 1000) -> str:
    with log_file.open("rb") as fh:
        fh.seek(0, 2)
        size = fh.tell()
        seek = max(0, size - max_chars * 4)
        fh.seek(seek)
        data = fh.read()
    return data.decode("utf-8", errors="replace")[-max_chars:]


async def start_event_service(
    service_state: ServiceState,
    root: Path,
    kill_and_wait: Callable[..., Awaitable[str]],
) -> str:
    """Start event service as host subprocess via ``python -m event_store serve``."""
    socket_err = ensure_socket_dir()
    if socket_err:
        return socket_err
    ensure_event_service_config()
    cfg = load_event_service_config()

    ingest_sock = Path(_DEFAULT_INGEST_SOCK)
    query_sock = Path(_DEFAULT_QUERY_SOCK)
    db_path = _DEFAULT_DB

    def _pre_launch() -> str | None:
        fd = _acquire_lock(_LOCK_FILE)
        try:
            recorded_pid, _ = service_state._resolve_pid_file(_PID_FILE)
            if recorded_pid is not None and _validate_module_pid(
                recorded_pid, _MODULE_NAME
            ):
                return f"{_SERVICE_NAME} is already running."
            if _PID_FILE.exists():
                _PID_FILE.unlink(missing_ok=True)
            err = ensure_socket_dir()
            if err:
                return err
            for sock in (ingest_sock, query_sock):
                if sock.exists():
                    if not _safe_unlink_stale_socket(sock):
                        return f"{_SERVICE_NAME} is already running (socket in use: {sock})."
            return None
        finally:
            _release_lock(fd)

    async with _get_start_lock():
        loop = asyncio.get_running_loop()
        pre_result = await loop.run_in_executor(None, _pre_launch)
        if pre_result is not None:
            return pre_result

        venv_python = Path.home() / ".venvs" / "universal" / "bin" / "python"
        python = str(venv_python) if venv_python.exists() else "python3"
        _LOG_DIR.mkdir(parents=True, exist_ok=True)
        log_file = _LOG_DIR / _LOG_FILENAME
        env = build_service_env(root)
        libs_path = str(root / "libs")
        existing_pythonpath = env.get("PYTHONPATH", "")
        env["PYTHONPATH"] = (
            f"{libs_path}:{existing_pythonpath}" if existing_pythonpath else libs_path
        )
        apply_host_service_logging_env(
            env, log_dir=_LOG_DIR, log_filename=_LOG_FILENAME
        )

        cmd_args: list[str] = [
            python,
            "-m",
            _MODULE_NAME,
            "serve",
            "--db",
            db_path,
            "--sock",
            str(ingest_sock),
            "--query-sock",
            str(query_sock),
        ]

        if cfg is not None:
            cmd_args.extend(["--retention-days", str(cfg.retention_days)])
            cmd_args.extend(["--max-sessions", str(cfg.max_sessions)])
            if cfg.tcp_enabled:
                cmd_args.append("--tcp")
                cmd_args.extend(["--tcp-ingest-port", str(cfg.tcp_ingest_port)])
                cmd_args.extend(["--tcp-query-port", str(cfg.tcp_query_port)])

        with log_file.open("w") as log_fh:
            process = await asyncio.create_subprocess_exec(
                *cmd_args,
                env=env,
                cwd=str(root),
                stdout=log_fh,
                stderr=asyncio.subprocess.STDOUT,
                start_new_session=True,
            )

        try:
            exit_code = await asyncio.wait_for(process.wait(), timeout=3.0)
            tail = _read_log_tail(log_file)
            return f"{_SERVICE_NAME} failed (exit {exit_code}).\n{tail}"
        except TimeoutError:
            logger.info(
                "%s subprocess remained alive after startup probe", _SERVICE_NAME
            )
            fd = _acquire_lock(_LOCK_FILE)
            try:
                GATEWAY_DIR.mkdir(parents=True, exist_ok=True)
                _PID_FILE.write_text(str(process.pid) + "\n")
            finally:
                _release_lock(fd)
            msg = f"{_SERVICE_NAME} starting (PID {process.pid})."
            if cfg is not None and cfg.tcp_enabled:
                msg += (
                    f" TCP: ingest=:{cfg.tcp_ingest_port}, query=:{cfg.tcp_query_port}"
                )
            return msg


async def stop_event_service(
    service_state: ServiceState,
    root: Path,
    kill_and_wait: Callable[..., Awaitable[str]],
) -> str:
    """Stop event service gracefully via SIGTERM."""
    ingest_sock = Path(_DEFAULT_INGEST_SOCK)
    query_sock = Path(_DEFAULT_QUERY_SOCK)

    def _stop_critical_section() -> tuple[int | None, bool, str | None]:
        fd = _acquire_lock(_LOCK_FILE)
        try:
            recorded_pid, _ = service_state._resolve_pid_file(_PID_FILE)
            if recorded_pid is not None and _validate_module_pid(
                recorded_pid, _MODULE_NAME
            ):
                return recorded_pid, True, None
            _PID_FILE.unlink(missing_ok=True)
            fallback = _find_module_pid_by_cmdline(_MODULE_NAME)
            if fallback is not None:
                return fallback, False, None
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
        for sock in (ingest_sock, query_sock):
            if sock.exists():
                fd = _acquire_lock(_LOCK_FILE)
                try:
                    _safe_unlink_stale_socket(sock)
                finally:
                    _release_lock(fd)
        return f"{_SERVICE_NAME} is not running."

    result = await kill_and_wait(
        target_pid,
        _PID_FILE if had_pid_file else None,
        service_name=_SERVICE_NAME,
    )

    for sock in (ingest_sock, query_sock):
        if sock.exists():
            fd = _acquire_lock(_LOCK_FILE)
            try:
                _safe_unlink_stale_socket(sock)
            finally:
                _release_lock(fd)

    return result
