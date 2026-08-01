"""Process-level single-instance guard for the manage TUI.

``manage.sock`` liveness (``api_server._is_socket_alive``) prevents *socket*
orphaning, but it is checked only after Textual is already up and it cannot see
a second manage that has not yet reached ``on_mount``. An exclusive ``flock`` on
``/tmp/universal-protocol/manage.lock``, taken before the app runs, makes the
exclusion a property of the process rather than of the socket: two launches
race on the kernel lock, the loser exits before touching any shared state.

The lock is advisory and released by the kernel on process death, so a crashed
manage never leaves a stale lock behind.
"""

from __future__ import annotations

import fcntl
import json
import os
import socket
from pathlib import Path

from transport_utils import MANAGE_SOCKET

MANAGE_LOCK_PATH = Path("/tmp/universal-protocol/manage.lock")

# Bounded so a wedged peer cannot hang the launch path; the identity read is a
# courtesy detail on an error message, never a correctness dependency.
_WHOAMI_TIMEOUT_S = 0.5


class ManageAlreadyRunningError(RuntimeError):
    """Raised when another process holds the manage single-instance lock."""


def query_manage_whoami(
    sock_path: str = MANAGE_SOCKET, *, timeout_s: float = _WHOAMI_TIMEOUT_S
) -> dict | None:
    """Ask the live manage.sock owner who it is, or ``None`` if unreachable."""
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            sock.settimeout(timeout_s)
            sock.connect(sock_path)
            request = {"jsonrpc": "2.0", "id": 1, "method": "whoami", "params": {}}
            sock.sendall((json.dumps(request) + "\n").encode())
            raw = sock.makefile("rb").readline()
    except (OSError, ValueError):
        return None
    try:
        result = json.loads(raw).get("result")
    except (json.JSONDecodeError, AttributeError):
        return None
    return result if isinstance(result, dict) else None


def describe_conflict(lock_path: Path = MANAGE_LOCK_PATH) -> str:
    """Compose the operator-facing message for a refused launch."""
    lines = [
        f"error: another manage instance holds {lock_path}.",
        'Stop the other instance (quit its TUI, or `manage(action="quit")` '
        "via MCP) before launching this one.",
    ]
    identity = query_manage_whoami()
    if identity:
        lines.append(
            "holder: pid={pid} code_version={code_version} "
            "started={process_start_time}".format(
                pid=identity.get("pid", "?"),
                code_version=identity.get("code_version", "?"),
                process_start_time=identity.get("process_start_time", "?"),
            )
        )
    return "\n".join(lines) + "\n"


def acquire_manage_lock(lock_path: Path = MANAGE_LOCK_PATH) -> int:
    """Take the exclusive non-blocking manage lock, returning the held fd.

    Raises ``ManageAlreadyRunningError`` when another process owns it. The fd
    must stay open for the lifetime of the process — closing it drops the lock.
    """
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as exc:
        os.close(fd)
        raise ManageAlreadyRunningError(describe_conflict(lock_path)) from exc
    os.ftruncate(fd, 0)
    os.write(fd, f"{os.getpid()}\n".encode())
    return fd


def release_manage_lock(fd: int) -> None:
    """Release the lock on clean exit (kernel also drops it on process death)."""
    try:
        fcntl.flock(fd, fcntl.LOCK_UN)
    except OSError:
        pass
    try:
        os.close(fd)
    except OSError:
        pass
