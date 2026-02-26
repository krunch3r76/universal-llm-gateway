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

        # Force kill with SIGKILL — kill entire process tree.
        # ∀ engines (vLLM EngineCore, etc.) that spawn child subprocesses:
        # killing only the registered PID orphans children to PID 1, leaving
        # them alive and holding GPU VRAM. Collect children first, then kill all.
        logger.info(f"🔫 Sending SIGKILL to {model_id} (PID: {pid}) and its process tree")
        try:
            process = psutil.Process(pid)

            # Snapshot children before killing parent — after parent dies they
            # may be reparented to PID 1 and become harder to enumerate.
            try:
                children = process.children(recursive=True)
            except psutil.NoSuchProcess:
                logger.info(f"📤 Process {pid} already terminated before tree collection")
                return True

            if children:
                pids = [c.pid for c in children]
                logger.info(f"🌲 Found {len(children)} child process(es) for {model_id} (PIDs: {pids}) — killing tree")

            process.kill()

            sigkill_timeout = float(
                getattr(
                    gateway_config.process_isolation,
                    "sigkill_wait_timeout",
                    3,
                )
            )
            try:
                _ = process.wait(timeout=sigkill_timeout)
                logger.info(f"✅ Process {pid} killed with SIGKILL")
            except psutil.TimeoutExpired:
                logger.error(
                    f"❌ Process {pid} didn't respond to SIGKILL after {sigkill_timeout}s"
                )
                return False
            except psutil.NoSuchProcess:
                logger.info(f"📤 Process {pid} already terminated during SIGKILL")

            # Kill any children that survived or were orphaned during parent kill.
            for child in children:
                try:
                    if child.is_running():
                        logger.warning(f"🔫 Killing orphaned child PID {child.pid} ({child.name()}) for {model_id}")
                        child.kill()
                        _ = child.wait(timeout=sigkill_timeout)
                except psutil.NoSuchProcess:
                    pass
                except psutil.TimeoutExpired:
                    logger.error(
                        f"❌ Child PID {child.pid} didn't respond to SIGKILL"
                    )

            return True

        except psutil.NoSuchProcess:
            logger.info(f"📤 Process {pid} already terminated during SIGKILL")
            return True

    except Exception as e:
        logger.error(f"❌ Error force-killing process {pid} for {model_id}: {e}")
        return False
