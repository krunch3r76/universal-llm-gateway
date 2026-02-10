"""
Socket utilities for safe file operations.

This module provides utilities for safely deleting socket files
with proper locking to prevent race conditions.
"""

import fcntl
from pathlib import Path

from universal_logging import get_logger

logger = get_logger(__name__)


def safe_delete_socket(socket_path: Path) -> bool:
    """
    Atomically delete socket file if it's orphaned.

    Uses file locking to prevent race conditions where another process
    might start using the socket between detection and deletion.

    Args:
        socket_path: Path to the socket file to delete

    Returns:
        True if socket was successfully deleted, False otherwise
    """
    try:
        # Try to get exclusive lock on socket file
        with open(socket_path) as f:
            fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            # If we got the lock, socket is orphaned - safe to delete
            socket_path.unlink()
            logger.debug(f"Successfully deleted orphaned socket: {socket_path}")
            return True

    except OSError as e:
        # Lock failed or file doesn't exist - socket is in use or already gone
        logger.debug(f"Could not delete socket {socket_path}: {e}")
        return False

    except Exception as e:
        # Unexpected error
        logger.warning(f"Unexpected error deleting socket {socket_path}: {e}")
        return False


def is_socket_in_use(socket_path: Path) -> bool:
    """
    Check if a socket file is currently in use by attempting to lock it.

    Args:
        socket_path: Path to the socket file

    Returns:
        True if socket is in use (lock failed), False if orphaned (lock succeeded)
    """
    try:
        with open(socket_path) as f:
            fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            # Lock succeeded - socket is orphaned
            return False

    except OSError:
        # Lock failed - socket is in use
        return True

    except Exception as e:
        # Unexpected error - assume in use for safety
        logger.debug(f"Error checking socket {socket_path}: {e}")
        return True
