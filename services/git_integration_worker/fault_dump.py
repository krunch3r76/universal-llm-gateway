"""On-demand thread stack dumps for the git-integration-worker process.

The worker runs a single uvicorn worker whose asyncio loop is shared by HTTP
handlers and every background task, so a loop starve wedges the socket
(Recv-Q climb) while the process stays alive. ``kill -USR1 <pid>`` dumps all
thread stacks — including the blocking frame — without killing the process,
which SIGQUIT/SIGABRT would.
"""

from __future__ import annotations

import faulthandler
import os
import signal
from pathlib import Path
from typing import IO

from universal_logging import get_logger

logger = get_logger(__name__)

_DUMP_DIR = Path(
    os.getenv("GIT_WORKER_STACKDUMP_DIR", "/tmp/logs/git-integration-worker")
)
_DUMP_FILENAME = "stackdump.log"

# faulthandler writes to the raw fd on signal delivery: the handle must stay
# open for the process lifetime, so it is held module-global rather than
# closed by the caller.
_dump_handle: IO[str] | None = None


def arm_stack_dumps() -> Path | None:
    """Register SIGUSR1 as an all-threads stack dump; return the dump path."""
    global _dump_handle
    if _dump_handle is not None:
        return Path(_dump_handle.name)
    if not hasattr(faulthandler, "register"):
        return None
    try:
        _DUMP_DIR.mkdir(parents=True, exist_ok=True)
        handle = (_DUMP_DIR / _DUMP_FILENAME).open("a", buffering=1)
    except OSError as exc:
        logger.warning("stack-dump arming failed: %s", exc)
        return None
    faulthandler.enable()
    faulthandler.register(signal.SIGUSR1, file=handle, all_threads=True, chain=False)
    _dump_handle = handle
    path = Path(handle.name)
    logger.info("SIGUSR1 stack dumps armed: pid=%d path=%s", os.getpid(), path)
    return path
