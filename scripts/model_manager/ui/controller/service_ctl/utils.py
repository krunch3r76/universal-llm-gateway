"""Utility functions shared across service control modules.

Part of the model_manager UI controller. Provides socket, lock, and
uvicorn PID helpers used by RAG and Cloud Proxy lifecycle management.
"""

from __future__ import annotations

import fcntl
import logging
import os
import socket as socket_mod
import stat as stat_mod
from pathlib import Path

import psutil

logger = logging.getLogger(__name__)


def _acquire_lock(lock_file: Path) -> int:
    """Acquire exclusive lock on a lockfile. Returns fd. Blocking."""
    lock_file.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(lock_file), os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
    except Exception:
        os.close(fd)
        raise
    return fd


def _release_lock(fd: int) -> None:
    """Release a file lock and close the file descriptor."""
    try:
        fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


def _validate_uvicorn_pid(pid: int, app_module: str, *, uds_mode: bool) -> bool:
    """Check PID is alive and cmdline matches uvicorn app; verify transport args."""
    try:
        proc = psutil.Process(pid)
        if not proc.is_running():
            return False
        cmdline = proc.cmdline() or []
        cmd_str = " ".join(cmdline)
        if app_module not in cmd_str:
            return False
        if uds_mode:
            return "--uds" in cmd_str
        return "--host" in cmd_str and "--port" in cmd_str
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return False


def _find_uvicorn_pid_by_cmdline(app_module: str, *, uds_mode: bool) -> int | None:
    """Find uvicorn process by cmdline match; verify transport args."""
    for proc in psutil.process_iter(["pid", "cmdline"]):
        try:
            cmdline = proc.info.get("cmdline") or []
            cmd_str = " ".join(str(c) for c in cmdline)
            if app_module not in cmd_str:
                continue
            if uds_mode and "--uds" in cmd_str:
                return proc.info["pid"]
            if not uds_mode and "--host" in cmd_str and "--port" in cmd_str:
                return proc.info["pid"]
        except (psutil.NoSuchProcess, psutil.AccessDenied, TypeError):
            continue
    return None


def _validate_module_pid(pid: int, module_name: str, subcmd: str = "serve") -> bool:
    """Check PID is alive and cmdline matches ``python -m module_name subcmd``."""
    try:
        proc = psutil.Process(pid)
        if not proc.is_running():
            return False
        cmdline = proc.cmdline() or []
        cmd_str = " ".join(cmdline)
        return module_name in cmd_str and subcmd in cmd_str
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return False


def _find_module_pid_by_cmdline(module_name: str, subcmd: str = "serve") -> int | None:
    """Find process by cmdline containing ``module_name`` and ``subcmd``."""
    for proc in psutil.process_iter(["pid", "cmdline"]):
        try:
            cmdline = proc.info.get("cmdline") or []
            cmd_str = " ".join(str(c) for c in cmdline)
            if module_name in cmd_str and subcmd in cmd_str:
                return proc.info["pid"]
        except (psutil.NoSuchProcess, psutil.AccessDenied, TypeError):
            continue
    return None


def _safe_unlink_stale_socket(socket_path: Path) -> bool:
    """Unlink socket only if stale: not actively listened on by any live process, S_ISSOCK.

    Uses connect() to probe liveness — if connect() succeeds the socket is live
    and must not be removed. ConnectionRefusedError means nobody is listening
    (stale) and the file is safe to unlink. bind() cannot distinguish a live
    socket from a stale one because it raises EADDRINUSE for any existing path.
    On lstat failure, attempts unlink anyway; unlink will raise if permissions
    prevent removal.

    Args:
        socket_path: Path to the Unix domain socket.

    Returns:
        True if unlinked or path did not exist; False if socket is in use or
        unlink failed.
    """
    if not socket_path.exists():
        return True
    try:
        st = socket_path.lstat()
        if not stat_mod.S_ISSOCK(st.st_mode):
            logger.warning(
                "Path %s is not a socket (mode=%o), skipping unlink",
                socket_path,
                st.st_mode,
            )
            return False
    except OSError as e:
        logger.warning(
            "Could not stat %s: %s, attempting unlink anyway", socket_path, e
        )
        # Fall through to unlink attempt

    try:
        with socket_mod.socket(socket_mod.AF_UNIX, socket_mod.SOCK_STREAM) as probe:
            probe.settimeout(0.5)
            probe.connect(str(socket_path))
        # connect() succeeded → live socket → do not unlink
        logger.debug(
            "Socket %s is in use by a live process, skipping unlink", socket_path
        )
        return False
    except ConnectionRefusedError:
        # Nobody listening → stale socket → fall through to unlink
        logger.debug("Socket probe for %s refused connection → stale", socket_path)
    except OSError as e:
        # FileNotFoundError, permission errors, etc. — attempt unlink
        logger.debug(
            "Socket probe failed for %s (%s), attempting unlink", socket_path, e
        )

    try:
        socket_path.unlink()
        logger.info("Unlinked stale socket %s", socket_path)
        return True
    except OSError as e:
        logger.warning("Could not unlink %s: %s", socket_path, e)
        return False
