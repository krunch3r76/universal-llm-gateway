"""
File locking utility for safe concurrent access to configuration files.

Uses fcntl.flock for POSIX-compliant advisory file locking.
"""

import fcntl
import time
from contextlib import contextmanager
from pathlib import Path

from universal_logging import get_logger

logger = get_logger(__name__)


class FileLockError(Exception):
    """Raised when file locking fails"""

    pass


class FileLock:
    """
    Context manager for advisory file locking using fcntl.flock.

    Ensures exclusive access to a file during write operations,
    preventing concurrent modifications and torn reads.

    Usage:
        with FileLock("/path/to/file.lock"):
            # Critical section - exclusive access guaranteed
            pass
    """

    def __init__(
        self,
        lock_file: str | Path,
        timeout: float | None = 30.0,
        blocking: bool = True,
    ):
        """
        Initialize file lock.

        Args:
            lock_file: Path to lock file (typically .lock extension)
            timeout: Maximum seconds to wait for lock (None = wait forever)
            blocking: If True, wait for lock; if False, raise immediately if locked
        """
        self.lock_file = Path(lock_file)
        self.timeout = timeout
        self.blocking = blocking
        self._lock_fd = None
        self._acquired = False

    def __enter__(self):
        """Acquire the lock"""
        self.acquire()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Release the lock"""
        self.release()
        return False

    def acquire(self):
        """
        Acquire exclusive lock on file.

        Raises:
            FileLockError: If lock cannot be acquired within timeout
        """
        # Ensure lock file directory exists
        self.lock_file.parent.mkdir(parents=True, exist_ok=True)

        # Open lock file (create if doesn't exist)
        self._lock_fd = open(self.lock_file, "w")

        start_time = time.time()

        try:
            if self.blocking:
                # Try to acquire lock with timeout
                while True:
                    try:
                        fcntl.flock(
                            self._lock_fd.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB
                        )
                        self._acquired = True
                        logger.debug(f"Acquired lock on {self.lock_file}")
                        return
                    except BlockingIOError:
                        # Lock is held by another process
                        if self.timeout is not None:
                            elapsed = time.time() - start_time
                            if elapsed >= self.timeout:
                                raise FileLockError(
                                    f"Failed to acquire lock on {self.lock_file} "
                                    f"after {self.timeout}s timeout"
                                )

                        # Wait a bit before retrying
                        time.sleep(0.1)
            else:
                # Non-blocking mode - fail immediately if locked
                try:
                    fcntl.flock(self._lock_fd.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    self._acquired = True
                    logger.debug(f"Acquired lock on {self.lock_file}")
                except BlockingIOError:
                    raise FileLockError(
                        f"Lock on {self.lock_file} is held by another process"
                    )

        except Exception as e:
            # Clean up on failure
            if self._lock_fd:
                self._lock_fd.close()
                self._lock_fd = None
            raise FileLockError(f"Failed to acquire lock on {self.lock_file}: {e}")

    def release(self):
        """Release the lock"""
        if self._acquired and self._lock_fd:
            try:
                fcntl.flock(self._lock_fd.fileno(), fcntl.LOCK_UN)
                self._acquired = False
                logger.debug(f"Released lock on {self.lock_file}")
            except Exception as e:
                logger.error(f"Error releasing lock on {self.lock_file}: {e}")
            finally:
                if self._lock_fd:
                    self._lock_fd.close()
                    self._lock_fd = None
                    # Clean up lock file after releasing
                    try:
                        self.lock_file.unlink(missing_ok=True)
                    except Exception as e:
                        logger.warning(
                            f"Could not remove lock file {self.lock_file}: {e}"
                        )


@contextmanager
def file_lock(lock_file: str | Path, timeout: float | None = 30.0):
    """
    Convenience context manager for file locking.

    Args:
        lock_file: Path to lock file
        timeout: Maximum seconds to wait for lock

    Example:
        with file_lock("config/model_loaders.yaml.lock"):
            # Safe to modify config file
            pass
    """
    lock = FileLock(lock_file, timeout=timeout)
    try:
        lock.acquire()
        yield lock
    finally:
        lock.release()
