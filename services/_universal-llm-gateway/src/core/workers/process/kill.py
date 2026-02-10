"""
Process termination utilities for worker lifecycle.

Handles SIGTERM/SIGKILL policies and OS-level process control.
"""

from typing import Any

from universal_logging import get_logger

try:
    import psutil
except ImportError:
    psutil = None
    logging.warning("psutil not available - process cleanup will be limited")

logger = get_logger(__name__)


async def force_kill_process(
    pid: int,
    model_id: str,
    gateway_config: Any,
) -> bool:
    """
    Force kill a process by PID with SIGKILL.

    Respects configuration for fast unload (skip SIGTERM) and timeouts.

    Args:
        pid: Process ID to kill
        model_id: Model identifier (for logging)
        gateway_config: Gateway configuration for kill policy

    Returns:
        True if process terminated successfully, False otherwise
    """
    try:
        if not psutil:
            logger.error("❌ psutil not available - cannot force kill process")
            return False

        # Check if process still exists
        if not psutil.pid_exists(pid):
            logger.info(f"📤 Process {pid} for {model_id} already terminated")
            return True

        # Check if we should skip SIGTERM for fast unloading
        skip_sigterm = getattr(
            gateway_config.process_isolation, "skip_sigterm_on_unload", True
        )
        fast_unload = getattr(
            gateway_config.process_isolation, "fast_model_unload", True
        )

        if not skip_sigterm and not fast_unload:
            # Try SIGTERM first (original behavior)
            logger.info(f"📤 Sending SIGTERM to {model_id} (PID: {pid})")
            try:
                process = psutil.Process(pid)
                process.terminate()

                # Wait for graceful termination using configurable timeout
                sigterm_timeout = float(
                    getattr(
                        gateway_config.process_isolation,
                        "sigterm_wait_timeout",
                        5,
                    )
                )
                try:
                    process.wait(timeout=sigterm_timeout)
                    logger.info(f"✅ Process {pid} terminated gracefully")
                    return True
                except psutil.TimeoutExpired:
                    logger.warning(
                        f"⚠️ Process {pid} didn't terminate gracefully, using SIGKILL"
                    )

            except psutil.NoSuchProcess:
                logger.info(f"📤 Process {pid} already terminated during SIGTERM")
                return True
        else:
            # Fast unload: skip SIGTERM and go directly to SIGKILL
            logger.info(
                f"🚀 Fast unload enabled - skipping SIGTERM for {model_id} (PID: {pid})"
            )

        # Force kill with SIGKILL
        logger.info(f"🔫 Sending SIGKILL to {model_id} (PID: {pid})")
        try:
            process = psutil.Process(pid)
            process.kill()

            # Wait for force kill using configurable timeout
            sigkill_timeout = float(
                getattr(
                    gateway_config.process_isolation,
                    "sigkill_wait_timeout",
                    3,
                )
            )
            try:
                process.wait(timeout=sigkill_timeout)
                logger.info(f"✅ Process {pid} killed with SIGKILL")
                return True
            except psutil.TimeoutExpired:
                logger.error(
                    f"❌ Process {pid} didn't respond to SIGKILL after {sigkill_timeout}s"
                )
                return False

        except psutil.NoSuchProcess:
            logger.info(f"📤 Process {pid} already terminated during SIGKILL")
            return True

    except Exception as e:
        logger.error(f"❌ Error force-killing process {pid} for {model_id}: {e}")
        return False
