"""Process liveness verification for tracked worker supervisors."""

import os
import time
from collections.abc import Callable
from typing import Any

from universal_logging import get_logger

from ..state import ProcessState

try:
    import psutil
except ImportError:
    psutil = None

logger = get_logger(__name__)


async def verify_process_alive(
    model_id: str,
    get_all_process_info_func: Callable[[], dict[str, Any]],
) -> bool:
    """Verify if a process is actually alive by checking its PID."""
    try:
        all_processes = get_all_process_info_func()
        if model_id not in all_processes:
            return False

        process_info = all_processes[model_id]
        pid = (
            getattr(process_info, "pid", None)
            if hasattr(process_info, "pid")
            else process_info.get("pid")
            if isinstance(process_info, dict)
            else None
        )

        if not pid:
            return False

        if psutil:
            return psutil.pid_exists(pid)

        try:
            os.kill(pid, 0)
            return True
        except (OSError, ProcessLookupError):
            return False

    except Exception as e:
        logger.debug(f"Error verifying process {model_id}: {e}")
        return False


async def is_process_alive(state: ProcessState, model_id: str) -> bool:
    """Simple liveness check - is the process responsive?"""
    supervisor = state.get_supervisor(model_id)
    if not supervisor:
        return False

    try:
        ping_command = {"command_type": "ping", "timestamp": time.time()}
        response = await supervisor.execute_command(ping_command, timeout=3.0)
        result = response
        return result and result.get("status") == "pong"

    except Exception as e:
        logger.debug(f"Process liveness check failed for {model_id}: {e}")
        return False
