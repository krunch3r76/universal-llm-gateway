"""Process cleanup helpers for stale, orphaned, and untracked worker processes."""

import asyncio
import logging
from typing import Any

from universal_logging import get_logger

from ...utils import get_universal_protocol_socket_path
from ..state import ProcessState

try:
    import psutil
except ImportError:
    psutil = None
    logging.warning("psutil not available - process cleanup will be limited")

logger = get_logger(__name__)


async def kill_pid_tree(pid: int, model_id: str) -> bool:
    """Kill a process and its children by PID. Last-resort cleanup."""
    if psutil is None:
        logger.warning(
            f"psutil not available, cannot kill PID tree for {model_id} (PID: {pid})"
        )
        return False

    try:
        proc = psutil.Process(pid)
        children = proc.children(recursive=True)
        proc.kill()
        for child in children:
            try:
                child.kill()
            except psutil.NoSuchProcess:
                pass
        psutil.wait_procs([proc] + children, timeout=5.0)
        logger.info(f"✅ Killed PID {pid} + {len(children)} children for {model_id}")
        return True
    except psutil.NoSuchProcess:
        return True
    except Exception as e:
        logger.error(f"❌ Failed to kill PID {pid} for {model_id}: {e}")
        return False


def _schedule_socket_cleanup(model_id: str, socket_path: str) -> None:
    from ....events import get_event_bus
    from ..communication import SocketCleanupRequested

    try:
        loop = asyncio.get_running_loop()
        loop.call_soon(
            lambda: asyncio.create_task(
                get_event_bus().publish_nowait(
                    SocketCleanupRequested(model_id=model_id, socket_path=socket_path)
                )
            )
        )
    except RuntimeError:
        pass


async def fallback_process_cleanup(manager: Any, model_id: str) -> bool:
    """Fallback cleanup for untracked processes using event-driven socket cleanup."""
    try:
        logger.info(f"🧹 Performing fallback cleanup for {model_id}")

        try:
            import psutil as psutil_mod

            for proc in psutil_mod.process_iter(["pid", "cmdline"]):
                try:
                    cmdline = proc.info["cmdline"]
                    if cmdline and len(cmdline) > 2:
                        if (
                            cmdline[1].endswith("worker.py")
                            and len(cmdline) > 2
                            and cmdline[2] == model_id
                        ):
                            logger.info(
                                f"🧹 Found orphaned worker process for {model_id} "
                                f"(PID: {proc.info['pid']}), terminating"
                            )
                            proc.terminate()
                            try:
                                sigterm_timeout = float(
                                    getattr(
                                        manager.gateway_config.process_isolation,
                                        "sigterm_wait_timeout",
                                        5,
                                    )
                                )
                                sigkill_timeout = float(
                                    getattr(
                                        manager.gateway_config.process_isolation,
                                        "sigkill_wait_timeout",
                                        3,
                                    )
                                )
                                proc.wait(timeout=sigterm_timeout)
                                logger.info(
                                    f"✅ Successfully terminated orphaned process {model_id}"
                                )
                            except psutil_mod.TimeoutExpired:
                                logger.warning(
                                    f"⚠️ Process {model_id} did not terminate gracefully, "
                                    "force killing"
                                )
                                proc.kill()
                                proc.wait(timeout=sigkill_timeout)
                            break
                except (psutil_mod.NoSuchProcess, psutil_mod.AccessDenied):
                    continue
        except ImportError:
            logger.warning("psutil not available for process cleanup")
        except Exception as e:
            logger.warning(f"Error during process cleanup: {e}")

        socket_path = get_universal_protocol_socket_path(model_id)
        _schedule_socket_cleanup(model_id, socket_path)
        return True

    except Exception as e:
        logger.error(f"❌ Error in fallback cleanup: {e}")
        return False


async def cleanup_stale_process(state: ProcessState, model_id: str) -> None:
    """Clean up a stale process using event-driven socket cleanup."""
    try:
        logger.info(f"🧹 Cleaning up stale process {model_id}")

        socket_path = get_universal_protocol_socket_path(model_id)
        _schedule_socket_cleanup(model_id, socket_path)

        state.remove_supervisor(model_id)
        state.remove_socket_path(model_id)

        logger.info(f"✅ Published cleanup event for {model_id}")

    except Exception as e:
        logger.warning(f"⚠️ Error cleaning up stale process {model_id}: {e}")
