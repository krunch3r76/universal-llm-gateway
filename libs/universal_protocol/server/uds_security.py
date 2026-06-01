"""Unix Domain Socket (UDS) security utilities.

Handles socket creation with restricted permissions, symlink rejection,
and path validation per MVP security spec (§3.2).

Security Model:
- Socket directory: /tmp/universal-protocol/ (worker-owned)
- Permissions: 0600 (owner-only, hard-coded)
- Symlink rejection: Check os.path.islink() before bind
- Path-length warning: Log if path ≥ 100 bytes
- Lifecycle: Unlink-before-bind, atexit cleanup
"""

import os
import socket
from pathlib import Path

from universal_logging import get_logger

from universal_protocol.config import get_config

logger = get_logger(__name__)


def ensure_socket_dir(
    socket_dir: str = None,
) -> Path:
    """Ensure socket directory exists with appropriate ownership.

    Creates the socket directory if it doesn't exist. Logs a warning if
    the directory already exists (assumed to be from a previous run).

    Args:
        socket_dir: Path to socket directory. If None, uses config value.

    Returns:
        Path object for the socket directory

    Raises:
        OSError: If directory cannot be created or is not a directory
    """
    if socket_dir is None:
        socket_dir = get_config().socket_dir

    socket_path = Path(socket_dir)

    if socket_path.exists():
        if not socket_path.is_dir():
            raise OSError(f"Socket path exists but is not a directory: {socket_dir}")
        logger.warning(
            f"Socket directory already exists (may be from previous run): {socket_dir}"
        )
    else:
        try:
            socket_path.mkdir(parents=True, exist_ok=True)
            logger.info(f"Created socket directory: {socket_dir}")
        except OSError as e:
            raise OSError(f"Failed to create socket directory {socket_dir}: {e}") from e

    return socket_path


def _validate_socket_path(socket_path: str) -> None:
    """Validate socket path for security concerns.

    Checks:
    - Path is not a symlink (symlink rejection)
    - Path length is reasonable (warn if ≥ 100 bytes)

    Args:
        socket_path: Full path to socket file

    Raises:
        OSError: If path is a symlink
    """
    # Reject symlinks (security)
    if os.path.islink(socket_path):
        raise OSError(f"Socket path is a symlink (rejected): {socket_path}")

    # Warn if path is too long (may hit system limits)
    if len(socket_path.encode("utf-8")) >= 100:
        logger.warning(
            f"Socket path is long ({len(socket_path)} bytes, "
            f"limit is ~108): {socket_path}"
        )


def bind_socket(
    socket_path: str,
    permissions: int = 0o600,
    unlink_first: bool = True,
) -> socket.socket:
    """Create and bind a Unix domain socket with security restrictions.

    Implements the MVP security model:
    - Rejects symlinks
    - Sets 0600 permissions (owner-only)
    - Unlinks stale socket before binding
    - Validates path length

    Args:
        socket_path: Full path to socket file (e.g.,
            /tmp/universal-protocol/worker-1.sock)
        permissions: File permissions (default: 0o600 for owner-only)
        unlink_first: Remove existing socket before binding (default: True)

    Returns:
        Bound socket.socket object (UDS)

    Raises:
        OSError: If socket creation/binding fails or path validation fails
        ValueError: If socket_path is empty or invalid

    Example:
        >>> sock = bind_socket("/tmp/universal-protocol/worker-1.sock")
        >>> sock.getsockname()
        '/tmp/universal-protocol/worker-1.sock'
        >>> sock.close()
    """
    if not socket_path or not isinstance(socket_path, str):
        raise ValueError(f"socket_path must be non-empty string, got {socket_path}")

    # Validate path before any file operations
    _validate_socket_path(socket_path)

    # Unlink existing socket if requested (standard pattern)
    if unlink_first and os.path.exists(socket_path):
        try:
            os.unlink(socket_path)
            logger.debug(f"Unlinked stale socket: {socket_path}")
        except OSError as e:
            logger.warning(
                f"Failed to unlink existing socket {socket_path}: {e}. Proceeding "
                f"anyway."
            )

    # Create Unix domain socket
    try:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    except OSError as e:
        raise OSError(f"Failed to create Unix socket: {e}") from e

    # Bind to path
    try:
        sock.bind(socket_path)
        logger.info(f"Bound Unix socket: {socket_path}")
    except OSError as e:
        sock.close()
        raise OSError(f"Failed to bind socket to {socket_path}: {e}") from e

    # Set permissions (owner-only)
    try:
        os.chmod(socket_path, permissions)
        logger.debug(f"Set socket permissions to {oct(permissions)}: {socket_path}")
    except OSError as e:
        sock.close()
        try:
            os.unlink(socket_path)
        except OSError:
            pass
        raise OSError(f"Failed to set socket permissions on {socket_path}: {e}") from e

    return sock


def socket_path_for_worker(
    worker_id: int,
    socket_dir: str = None,
) -> str:
    """Generate socket path template for a worker.

    Args:
        worker_id: Worker identifier (e.g., 1, 2, 3)
        socket_dir: Base socket directory. If None, uses config value.

    Returns:
        Full socket path (e.g., /tmp/universal-protocol/worker-1.sock)

    Example:
        >>> socket_path_for_worker(1)
        '/tmp/universal-protocol/worker-1.sock'
    """
    if socket_dir is None:
        socket_dir = get_config().socket_dir
    return os.path.join(socket_dir, f"worker-{worker_id}.sock")
