"""
Orphaned socket detection service.

This module provides a unified service for identifying orphaned socket files,
consolidating the logic used by both crash detection and cleanup handlers.
"""

import socket as socket_module
from pathlib import Path

from universal_logging import get_logger

logger = get_logger(__name__)


class OrphanedSocketDetector:
    """
    Service to identify orphaned socket files.

    Provides unified logic for determining which socket files are orphaned,
    used by both crash detection and cleanup handlers.
    """

    def __init__(self, worker_controller):
        """
        Initialize the orphan detector.

        Args:
            worker_controller: WorkerController instance for checking process status
        """
        self.worker_controller = worker_controller

    async def find_orphaned_sockets(
        self, socket_dir: Path
    ) -> list[tuple[str, Path, str]]:
        """
        Scan for orphaned sockets in a directory.

        A socket is considered orphaned if:
        - No supervisor exists for the model, OR
        - Supervisor exists but process is not running, OR
        - Socket file exists but is not accepting connections

        Args:
            socket_dir: Directory to scan for socket files

        Returns:
            List of (model_id, socket_path, reason) tuples for orphaned sockets
        """
        if not socket_dir.exists():
            return []

        socket_files = list(socket_dir.glob("*.sock"))
        orphaned = []

        for socket_file in socket_files:
            # Skip non-worker sockets (e.g., Stargate connection sockets)
            # Worker sockets are named: worker-{model_id}.sock
            if not socket_file.name.startswith("worker-"):
                logger.debug(
                    f"Skipping non-worker socket during orphan detection: {socket_file.name}"
                )
                continue
            
            # Extract model_id from "worker-{model_id}.sock"
            model_id = socket_file.stem.removeprefix("worker-")
            reason = await self._is_socket_orphaned(model_id, socket_file)

            if reason:
                orphaned.append((model_id, socket_file, reason))

        return orphaned

    async def _is_socket_orphaned(self, model_id: str, socket_file: Path) -> str | None:
        """
        Check if a specific socket file is orphaned.

        Args:
            model_id: Model ID associated with the socket
            socket_file: Path to the socket file

        Returns:
            Reason why socket is orphaned, or None if not orphaned
        """
        # Use public API to get worker info
        worker_info = self.worker_controller.get_worker_info(model_id)

        if not worker_info:
            return "No worker found for model"

        try:
            # Check if process is actually running
            if worker_info.status.value != "RUNNING":
                return f"Process status is {worker_info.status.value}, not RUNNING"

            # Check if socket is actually accepting connections
            if not self._is_socket_accepting_connections(socket_file):
                return "Socket file exists but not accepting connections"

            # Socket appears to be healthy
            return None

        except Exception as e:
            # If we can't check status, assume orphaned
            logger.debug(f"Could not check status for {model_id}: {e}")
            return f"Error checking process status: {e}"

    def _is_socket_accepting_connections(self, socket_file: Path) -> bool:
        """
        Test if a Unix socket is actively listening for connections.

        Uses a connection attempt with short timeout (0.1s). This may produce
        false negatives on heavily loaded systems where the listening process
        is slow to accept connections.

        Args:
            socket_file: Path to Unix socket file

        Returns:
            True if connection succeeds (socket is accepting connections)
            False if connection fails (socket is orphaned)
        """
        test_socket = None
        try:
            test_socket = socket_module.socket(
                socket_module.AF_UNIX, socket_module.SOCK_STREAM
            )
            test_socket.settimeout(0.1)
            test_socket.connect(str(socket_file))
            # Connection succeeded - socket is accepting connections
            return True

        except (OSError, ConnectionRefusedError):
            # Connection failed - socket is orphaned
            return False

        except Exception as e:
            # Unexpected error - log and assume orphaned for safety
            logger.warning(
                f"Unexpected error testing socket {socket_file}: {e}", exc_info=True
            )
            return False

        finally:
            if test_socket:
                try:
                    test_socket.close()
                except Exception:
                    pass  # Best effort cleanup

    def get_socket_stats(self, socket_dir: Path) -> dict:
        """
        Get statistics about socket files in a directory.

        Args:
            socket_dir: Directory to analyze

        Returns:
            Dict with socket statistics
        """
        if not socket_dir.exists():
            return {"total_sockets": 0, "orphaned_sockets": 0}

        socket_files = list(socket_dir.glob("*.sock"))
        total_sockets = len(socket_files)

        # Count orphaned sockets (synchronous check for stats)
        orphaned_count = 0
        for socket_file in socket_files:
            model_id = socket_file.stem
            worker_info = self.worker_controller.get_worker_info(model_id)

            if not worker_info or worker_info.status.value != "RUNNING":
                orphaned_count += 1

        return {
            "total_sockets": total_sockets,
            "orphaned_sockets": orphaned_count,
            "healthy_sockets": total_sockets - orphaned_count,
        }
