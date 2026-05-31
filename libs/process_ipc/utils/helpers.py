"""
Helper utilities for process-ipc package.

Provides common utility functions used throughout the package
for path management, networking, and other operations.
"""

import asyncio
import ctypes
import os
import signal
import socket
import sys
import tempfile
import uuid
from contextlib import contextmanager
from pathlib import Path

from universal_logging import get_logger

logger = get_logger(__name__)


def setup_parent_death_signal(death_signal: int = signal.SIGKILL) -> None:
    """
    Configure child process to receive a signal when parent dies (Linux only).

    Uses PR_SET_PDEATHSIG (prctl) to make the kernel automatically send
    a signal to this process if the parent process dies, even if the
    parent receives SIGKILL.

    This function should be called in the child process, typically via
    preexec_fn in subprocess.Popen.

    Args:
        death_signal: Signal to receive when parent dies (default: SIGKILL)

    Note:
        - Linux-only feature (no-op on other platforms)
        - Must be called in child process (after fork, before exec)
        - Survives exec() calls
        - Works even if parent receives SIGKILL
        - Prevents orphaned processes from holding resources
        - Handles race condition: exits immediately if parent died
          between fork() and prctl() (detects ppid == 1)

    Example:
        # In subprocess spawning:
        subprocess.Popen(
            cmd,
            preexec_fn=lambda: setup_parent_death_signal(signal.SIGKILL)
        )

    References:
        - man prctl(2) - PR_SET_PDEATHSIG section
        - https://linux.die.net/man/2/prctl
    """
    # Only works on Linux
    if sys.platform != "linux":
        logger.debug("PR_SET_PDEATHSIG not available on non-Linux platforms")
        return

    try:
        # PR_SET_PDEATHSIG = 1
        PR_SET_PDEATHSIG = 1  # noqa: N806 - system constant convention

        libc = ctypes.CDLL("libc.so.6", use_errno=True)
        result = libc.prctl(PR_SET_PDEATHSIG, death_signal)

        if result != 0:
            errno = ctypes.get_errno()
            logger.warning(f"prctl(PR_SET_PDEATHSIG) returned {result}, errno={errno}")
            return

        logger.debug(f"PR_SET_PDEATHSIG configured to send signal {death_signal}")

        in_container = Path("/proc/1/comm").read_text().strip() not in [
            "systemd",
            "init",
        ]

        # CRITICAL: Handle race condition where parent died between fork() and prctl()
        # If parent is now init (PID 1), we're already orphaned - exit immediately
        if os.getppid() == 1 and not in_container:
            logger.warning("Parent died during spawn (ppid=1), exiting immediately")
            os._exit(1)

    except Exception as e:
        # Don't fail if we can't set it - it's a safety feature
        logger.warning(f"Could not configure PR_SET_PDEATHSIG: {e}")


def setup_enhanced_orphan_prevention() -> None:
    """
    Enhanced orphan prevention with reliable kernel-level protection.

    This function sets up PR_SET_PDEATHSIG to ensure child processes
    are automatically killed when the parent dies, even if the parent
    receives SIGKILL.

    IMPORTANT: This function does NOT use os.setsid() because setsid()
    can interfere with PR_SET_PDEATHSIG in edge cases where the parent
    dies during the fork→setsid window. Instead, we use setpgid() for
    process group isolation which is sufficient for cleanup purposes.

    Should be called in preexec_fn for subprocess.Popen.

    Layers of protection:
    1. PR_SET_PDEATHSIG - Kernel sends SIGKILL when parent thread dies
    2. Process group isolation - setpgid(0, 0) for killpg() support
    """
    # Layer 1: Parent death signal (kernel-level protection)
    # This is the ONLY reliable kernel-level protection
    setup_parent_death_signal(signal.SIGKILL)

    # Layer 2: Process group isolation (NOT session - setsid() can break pdeathsig)
    # This allows parent to use os.killpg() to kill entire process group
    try:
        os.setpgid(0, 0)  # Create new process group, child becomes group leader
        logger.debug("Created new process group")
    except OSError as e:
        # Already a process group leader, or permissions issue - that's fine
        logger.debug(f"setpgid(0,0) failed (likely already group leader): {e}")


def setup_process_isolation_with_cgroups(cgroup_name: str | None = None) -> None:
    """
    Advanced process isolation using cgroups (requires root or cgroup permissions).

    Creates a dedicated cgroup for the process that can be easily cleaned up
    when the parent terminates. This is the most robust method but requires
    system-level permissions.

    Args:
        cgroup_name: Name for the cgroup (auto-generated if None)

    Note:
        - Requires cgroup v2 support
        - Requires write permissions to /sys/fs/cgroup
        - Falls back gracefully if not available
    """
    if sys.platform != "linux":
        return

    if cgroup_name is None:
        cgroup_name = f"universal-llm-worker-{os.getpid()}"

    try:
        cgroup_path = Path(f"/sys/fs/cgroup/{cgroup_name}")

        # Create cgroup directory
        cgroup_path.mkdir(exist_ok=True)

        # Add current process to cgroup
        (cgroup_path / "cgroup.procs").write_text(str(os.getpid()))

        # Set memory limit (optional - prevents runaway processes)
        # (cgroup_path / "memory.max").write_text("8G")

        logger.debug(f"Process added to cgroup: {cgroup_path}")

    except Exception as e:
        logger.debug(f"Could not set up cgroup isolation: {e}")
        # Fall back to standard process isolation
        setup_enhanced_orphan_prevention()


def generate_socket_path(
    base_dir: str | None = None,
    process_id: str | None = None,
    prefix: str = "process_ipc",
) -> str:
    """
    Generate a unique Unix socket path.

    Args:
        base_dir: Base directory for socket (defaults to temp directory)
        process_id: Process ID to include in path
        prefix: Prefix for socket filename

    Returns:
        str: Absolute path to Unix socket
    """
    if base_dir is None:
        base_dir = tempfile.gettempdir()

    base_path = Path(base_dir)
    ensure_directory_exists(base_path)

    if process_id:
        socket_name = f"{prefix}_{process_id}.sock"
    else:
        socket_name = f"{prefix}_{uuid.uuid4().hex[:8]}.sock"

    socket_path = base_path / socket_name

    # Check if socket file already exists and clean it up
    if socket_path.exists():
        try:
            os.unlink(socket_path)
            logger.warning(
                f"Removed existing socket file {socket_path} - "
                "previous session failed to clean up properly"
            )
        except OSError as e:
            logger.error(f"Failed to remove existing socket file {socket_path}: {e}")
            raise

    return str(socket_path)


def ensure_directory_exists(path: str | Path) -> None:
    """
    Ensure that a directory exists, creating it if necessary.

    Args:
        path: Directory path to create

    Raises:
        OSError: If directory cannot be created
    """
    path = Path(path)
    path.mkdir(parents=True, exist_ok=True)


def cleanup_socket_path(socket_path: str) -> bool:
    """
    Clean up a Unix socket file.

    Args:
        socket_path: Path to socket file to remove

    Returns:
        bool: True if cleanup was successful or file didn't exist
    """
    try:
        socket_file = Path(socket_path)
        if socket_file.exists():
            # Try to remove the socket file directly
            # If it's in use by an active process, this will fail
            # If it's stale (no active process), this will succeed
            try:
                socket_file.unlink()
                logger.debug(f"Socket file removed: {socket_path}")
                return True
            except OSError as e:
                if e.errno == 98:  # Address already in use
                    logger.warning(
                        f"Socket file is in use by active process, "
                        f"skipping cleanup: {socket_path}"
                    )
                    return False
                else:
                    logger.error(f"Failed to remove socket file {socket_path}: {e}")
                    return False
        return True
    except Exception as e:
        logger.error(f"Error in cleanup_socket_path for {socket_path}: {e}")
        return False


def is_port_available(port: int, host: str = "localhost") -> bool:
    """
    Check if a TCP port is available.

    Args:
        port: Port number to check
        host: Host to check port on

    Returns:
        bool: True if port is available
    """
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(1)
            result = sock.connect_ex((host, port))
            return result != 0
    except OSError:
        return False


def is_socket_path_available(socket_path: str) -> bool:
    """
    Check if a Unix socket path is available.

    Args:
        socket_path: Path to Unix socket

    Returns:
        bool: True if socket path is available
    """
    try:
        # Check if file exists
        socket_file = Path(socket_path)
        if not socket_file.exists():
            return True

        # If file exists, try to connect to see if it's active
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            sock.settimeout(1)
            result = sock.connect_ex(socket_path)
            return result != 0
    except OSError:
        return True


async def exponential_backoff(
    attempt: int,
    base_delay: float = 1.0,
    max_delay: float = 60.0,
    backoff_factor: float = 2.0,
) -> None:
    """
    Implement exponential backoff delay.

    Args:
        attempt: Current attempt number (0-based)
        base_delay: Initial delay in seconds
        max_delay: Maximum delay in seconds
        backoff_factor: Multiplier for exponential backoff
    """
    delay = min(base_delay * (backoff_factor**attempt), max_delay)
    await asyncio.sleep(delay)


def get_available_memory() -> int:
    """
    Get available system memory in bytes.

    Returns:
        int: Available memory in bytes
    """
    try:
        # Linux/Unix systems
        with open("/proc/meminfo") as f:
            for line in f:
                if line.startswith("MemAvailable:"):
                    # Value is in kB
                    return int(line.split()[1]) * 1024
    except (FileNotFoundError, IndexError, ValueError):
        pass

    # Fallback for other systems
    import psutil

    return psutil.virtual_memory().available


def get_process_info(pid: int) -> dict | None:
    """
    Get information about a running process.

    Args:
        pid: Process ID

    Returns:
        dict: Process information or None if process not found
    """
    try:
        import psutil

        process = psutil.Process(pid)
        return {
            "pid": process.pid,
            "name": process.name(),
            "status": process.status(),
            "cpu_percent": process.cpu_percent(),
            "memory_percent": process.memory_percent(),
            "create_time": process.create_time(),
            "cmdline": process.cmdline(),
        }
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return None


def validate_socket_path(socket_path: str) -> tuple[bool, str]:
    """
    Validate that a socket path is suitable for use.

    Args:
        socket_path: Path to validate

    Returns:
        tuple: (is_valid, error_message)
    """
    path = Path(socket_path)

    # Check if path is too long for Unix socket
    if len(socket_path) > 108:  # Unix socket path limit
        return False, f"Socket path too long (max 108 characters): {len(socket_path)}"

    # Check if parent directory is writable
    parent_dir = path.parent
    if not parent_dir.exists():
        try:
            parent_dir.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            return False, f"Cannot create parent directory: {e}"

    if not os.access(parent_dir, os.W_OK):
        return False, f"Parent directory is not writable: {parent_dir}"

    # Check if existing file is a socket
    if path.exists() and not path.is_socket():
        return False, f"Path exists but is not a socket: {socket_path}"

    return True, ""


def format_bytes(size: int) -> str:
    """
    Format byte count as human-readable string.

    Args:
        size: Size in bytes

    Returns:
        str: Formatted size string
    """
    size_float = float(size)
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if size_float < 1024.0:
            return f"{size_float:.1f} {unit}"
        size_float /= 1024.0
    return f"{size_float:.1f} PB"


# generate_correlation_id removed - use core.messages.generate_correlation_id instead


@contextmanager
def temporary_socket_path(process_id: str | None = None, prefix: str = "process_ipc"):
    """
    Context manager for temporary socket paths with guaranteed cleanup.

    Creates a temporary directory that is automatically cleaned up when
    the context exits, even if the process crashes or is killed.
    This prevents leftover socket files.

    Args:
        process_id: Process ID to include in path
        prefix: Prefix for socket filename

    Yields:
        str: Path to temporary socket file

    Example:
        with temporary_socket_path("worker_123") as socket_path:
            # Use socket_path for your socket
            # Socket file is automatically cleaned up when exiting the context
    """
    # Create a temporary directory that will be cleaned up automatically
    with tempfile.TemporaryDirectory(prefix="process_ipc_") as temp_dir:
        if process_id:
            socket_name = f"{prefix}_{process_id}.sock"
        else:
            socket_name = f"{prefix}_{uuid.uuid4().hex[:8]}.sock"

        socket_path = os.path.join(temp_dir, socket_name)

        # Clean up any existing socket file
        # (shouldn't happen in temp dir, but just in case)
        if os.path.exists(socket_path):
            try:
                os.unlink(socket_path)
                logger.warning(f"Removed existing socket file: {socket_path}")
            except OSError as e:
                logger.error(
                    f"Failed to remove existing socket file {socket_path}: {e}"
                )
                raise

        yield socket_path
        # Socket file is automatically cleaned up when temp_dir is cleaned up


@contextmanager
def managed_socket_path(
    socket_path: str | None = None,
    process_id: str | None = None,
    prefix: str = "process_ipc",
):
    """
    Context manager that handles both client-specified and auto-generated socket paths.

    If socket_path is provided, it uses that path and ensures cleanup.
    If socket_path is None, it generates a temporary socket path with automatic cleanup.

    Args:
        socket_path: Client-specified socket path (optional)
        process_id: Process ID to include in path (used if socket_path is None)
        prefix: Prefix for socket filename (used if socket_path is None)

    Yields:
        str: Path to socket file (either provided or generated)

    Example:
        # Client-specified path
        with managed_socket_path("/tmp/my_socket.sock") as socket_path:
            # Use the specified socket path
            # Cleanup is handled automatically

        # Auto-generated path
        with managed_socket_path(process_id="worker_123") as socket_path:
            # Use the generated socket path
            # Cleanup is handled automatically
    """
    if socket_path is not None:
        # Client specified a socket path - use it and ensure cleanup
        try:
            # Clean up any existing socket file
            if os.path.exists(socket_path):
                try:
                    os.unlink(socket_path)
                    logger.warning(f"Removed existing socket file: {socket_path}")
                except OSError as e:
                    logger.error(
                        f"Failed to remove existing socket file {socket_path}: {e}"
                    )
                    raise

            yield socket_path

        finally:
            # Clean up the client-specified socket file
            try:
                if os.path.exists(socket_path):
                    os.unlink(socket_path)
                    logger.debug(
                        f"Cleaned up client-specified socket file: {socket_path}"
                    )
            except OSError as e:
                logger.warning(f"Failed to clean up socket file {socket_path}: {e}")
    else:
        # No socket path specified - use temporary socket with automatic cleanup
        with temporary_socket_path(process_id, prefix) as temp_socket_path:
            yield temp_socket_path
