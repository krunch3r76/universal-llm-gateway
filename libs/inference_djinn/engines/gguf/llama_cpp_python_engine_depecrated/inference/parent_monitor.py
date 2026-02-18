"""
Parent death monitor for graceful inference cancellation.

Detects when parent process dies (ppid becomes 1) and triggers
abort callback for graceful C-level cancellation before the
kernel delivers SIGKILL via PR_SET_PDEATHSIG.

Invariant: ∀ monitor_task: running(monitor_task) ⟹ ◇(parent_dead ∨ cancelled)
"""

import asyncio
import os
from collections.abc import Callable
from pathlib import Path

from universal_logging import get_logger

logger = get_logger(__name__)

# Check interval - balance between responsiveness and overhead
# Increased to 2.0s to reduce overhead during inference in containers
PARENT_CHECK_INTERVAL_SECONDS = 2.0  # 2 seconds


def _is_in_container() -> bool:
    """
    Detect if running inside a container.

    In containers, PID 1 is the container's init process (e.g., "python3", "bash"),
    not the system init. Worker processes may legitimately have ppid=1 if they're
    direct children of the container init.

    Returns:
        True if in container (ppid=1 is normal), False if bare metal (ppid=1 means orphaned)
    """
    try:
        proc_1_comm = Path("/proc/1/comm").read_text().strip()
        # In bare metal, PID 1 is systemd/init
        # In containers, PID 1 is the container's entrypoint (python3, bash, etc.)
        return proc_1_comm not in ["systemd", "init"]
    except Exception:
        # If we can't read /proc/1/comm, assume bare metal (safer default)
        return False


async def monitor_parent_death(
    abort_trigger: Callable[[], None],
    cancellation_event: asyncio.Event | None = None,
    check_interval: float = PARENT_CHECK_INTERVAL_SECONDS,
) -> None:
    """
    Monitor for parent process death and trigger abort.

    Runs until:
    - Parent dies (ppid == 1 on bare metal) → triggers abort and returns
    - Task is cancelled → returns

    Args:
        abort_trigger: Callable to trigger abort (usually AbortController.trigger)
        cancellation_event: Optional event to set when parent dies
        check_interval: Seconds between parent PID checks

    Note:
        This provides graceful cancellation ~200ms before PR_SET_PDEATHSIG
        delivers SIGKILL. The abort callback stops llama.cpp at the next
        token boundary.

        Container-aware: In Docker/containers, ppid=1 is normal (container init),
        not orphaning. Only triggers on ppid=1 on bare metal systems.
    """
    initial_ppid = os.getppid()
    in_container = _is_in_container()

    if in_container:
        logger.debug(
            f"Running in container (ppid={initial_ppid}), parent death monitor will not trigger on ppid=1"
        )
        # In containers, parent death monitoring is less critical since:
        # - Container orchestration handles process cleanup
        # - ppid=1 is normal (container init), not orphaning
        # Skip monitoring entirely to avoid overhead
        logger.debug("Skipping parent death monitoring in container environment")
        try:
            # Wait forever without polling (cancelled when inference completes)
            never_set = asyncio.Event()
            _ = await never_set.wait()
        except asyncio.CancelledError:
            pass
        return

    try:
        while True:
            await asyncio.sleep(check_interval)

            current_ppid = os.getppid()

            # CRITICAL: Only treat ppid=1 as parent death on bare metal
            # In containers, ppid=1 is normal (worker is child of container init)
            if current_ppid == 1 and not in_container:
                # Parent died - we're now orphaned (init adopted us)
                logger.warning(
                    f"🛑 Parent died (was {initial_ppid}, now {current_ppid}=init), triggering graceful abort"
                )

                # Trigger C-level abort callback
                abort_trigger()

                # Also set cancellation event if provided
                if cancellation_event is not None:
                    cancellation_event.set()

                return

    except asyncio.CancelledError:
        # Normal cancellation when inference completes
        pass
