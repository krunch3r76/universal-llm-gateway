"""Cortex API service lifecycle — start, stop, PID/socket management.

Manages the cortex-api uvicorn process on UDS with lock files
and stale socket cleanup. Uses the shared _start/_stop_uvicorn_service
pattern (single socket, standard FastAPI app).
"""

from __future__ import annotations

import compileall
import io
import logging
import os
from collections.abc import Awaitable, Callable
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from ...model.service_state import ServiceState
from ..service_config import GATEWAY_DIR, load_mcp_config
from .uvicorn_service import _start_uvicorn_service, _stop_uvicorn_service

_logger = logging.getLogger(__name__)
_DISPATCH_OPS_DIR = (
    Path(__file__).resolve().parents[5] / "libs" / "cortex_store" / "dispatch_ops"
)


def _smoke_check_dispatch_ops() -> str | None:
    """Compile dispatch_ops/ to surface NameErrors / SyntaxErrors before startup.

    Returns None on clean compile, or a human-readable error string otherwise.
    `compileall` only catches syntax-level issues, but those have already
    bitten us once — F1 in todo:session-close-friction-audit was a real
    NameError introduced by a refactor. SyntaxErrors are caught here; pure
    NameErrors in rarely-hit branches still slip through Python's compile
    phase, so callers should pair this with the preflight dispatch op.
    """
    if not _DISPATCH_OPS_DIR.is_dir():
        return None
    buf_out, buf_err = io.StringIO(), io.StringIO()
    with redirect_stdout(buf_out), redirect_stderr(buf_err):
        ok = compileall.compile_dir(
            str(_DISPATCH_OPS_DIR), quiet=1, force=True, legacy=True
        )
    if ok:
        return None
    return (
        f"compileall failed for {_DISPATCH_OPS_DIR}:\n"
        f"{buf_out.getvalue()}{buf_err.getvalue()}"
    )


_CORTEX_APP_MODULE = "cortex_store.main:app"
_CORTEX_PID_FILE = GATEWAY_DIR / "cortex-api.pid"
_CORTEX_LOCK_FILE = GATEWAY_DIR / "cortex-api.lock"
_CORTEX_SOCKET = Path(
    os.environ.get("CORTEX_API_SOCK", "/tmp/universal-protocol/cortex-api.sock")
)
_CORTEX_LOG_DIR = Path("/tmp/logs/cortex-api")


async def start_cortex_api(
    service_state: ServiceState,
    root: Path,
    kill_and_wait: Callable[..., Awaitable[str]],  # noqa: ARG001
) -> str:
    """Start cortex-api as host process via uvicorn on UDS."""
    smoke_error = _smoke_check_dispatch_ops()
    if smoke_error is not None:
        _logger.error("Cortex API dispatch_ops smoke check failed:\n%s", smoke_error)
        return f"Cortex API not started — dispatch_ops compile error:\n{smoke_error}"
    # CORTEX_FILES_ROOT must match the data_dir that the MCP Docker container
    # bind-mounts as /data/files (set via MCP_DATA_DIR in build_mcp_env).
    # Without this, session_close writes transcripts to ~/mcp-data/files while
    # the container's fs(cortex) sandbox reads from the configured data_dir —
    # a silent mismatch that produces confirmed session entities with no file.
    extra_env: dict[str, str] | None = None
    cfg = load_mcp_config()
    if cfg is not None:
        extra_env = {
            "CORTEX_FILES_ROOT": str(Path(cfg.data_dir).expanduser() / "files")
        }
    return await _start_uvicorn_service(
        service_state=service_state,
        root=root,
        app_module=_CORTEX_APP_MODULE,
        pid_file=_CORTEX_PID_FILE,
        lock_file=_CORTEX_LOCK_FILE,
        service_name="Cortex API",
        socket_path=_CORTEX_SOCKET,
        tcp_config=None,
        log_dir=_CORTEX_LOG_DIR,
        log_filename="cortex-api.log",
        extra_env=extra_env,
    )


async def stop_cortex_api(
    service_state: ServiceState,
    root: Path,  # noqa: ARG001
    kill_and_wait: Callable[..., Awaitable[str]],
) -> str:
    """Stop cortex-api gracefully."""
    return await _stop_uvicorn_service(
        service_state=service_state,
        kill_and_wait=kill_and_wait,
        pid_file=_CORTEX_PID_FILE,
        lock_file=_CORTEX_LOCK_FILE,
        service_name="Cortex API",
        socket_path=_CORTEX_SOCKET,
        tcp_config=None,
        app_module=_CORTEX_APP_MODULE,
    )
