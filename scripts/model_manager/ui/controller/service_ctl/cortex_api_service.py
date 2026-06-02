"""Cortex API service lifecycle — start, stop, PID/socket management.

Manages the cortex-api uvicorn process on UDS with lock files
and stale socket cleanup. Uses the shared _start/_stop_uvicorn_service
pattern (single socket, standard FastAPI app).
"""

from __future__ import annotations

import compileall
import io
import os
import subprocess
from collections.abc import Awaitable, Callable
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from universal_logging import get_logger

from ...model.service_state import ServiceState
from ..service_config import GATEWAY_DIR, build_service_env, load_mcp_config
from .uvicorn_service import _start_uvicorn_service, _stop_uvicorn_service

_logger = get_logger(__name__)
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
# Browser-facing HTTP for /control-tower. Port 8200 is often cloud-proxy or
# other docker services on this host; cortex-api itself stays on UDS for MCP.
_CORTEX_HTTP_HOST = os.environ.get("CORTEX_API_HTTP_HOST", "127.0.0.1")
_CORTEX_HTTP_PORT = int(os.environ.get("CORTEX_API_HTTP_PORT", "8202"))
_CORTEX_HTTP_PID_FILE = GATEWAY_DIR / "cortex-api-http.pid"


def _build_cortex_runtime_env(root: Path) -> dict[str, str]:
    """Env overrides shared by UDS cortex-api and the HTTP /control-tower forwarder."""
    extra_env: dict[str, str] = {}
    cfg = load_mcp_config()
    if cfg is not None:
        extra_env["CORTEX_FILES_ROOT"] = str(Path(cfg.data_dir).expanduser() / "files")
        if cfg.agent_bus_token:
            extra_env["AGENT_BUS_TOKEN"] = cfg.agent_bus_token
    transcripts_root = os.environ.get("CURSOR_AGENT_TRANSCRIPTS_ROOT")
    if not transcripts_root:
        transcripts_root = str(
            Path.home()
            / ".cursor"
            / "projects"
            / "mnt-torus-projects-universal-llm-gateway"
            / "agent-transcripts"
        )
    extra_env["CURSOR_AGENT_TRANSCRIPTS_ROOT"] = transcripts_root
    return extra_env


def _http_forwarder_cmdline(pid: int) -> str:
    try:
        return (
            Path(f"/proc/{pid}/cmdline")
            .read_bytes()
            .replace(b"\0", b" ")
            .decode(errors="replace")
        )
    except OSError:
        return ""


def _http_forwarder_running() -> bool:
    """True when pid file points at our uvicorn on the control-tower port."""
    if not _CORTEX_HTTP_PID_FILE.exists():
        return False
    try:
        pid = int(_CORTEX_HTTP_PID_FILE.read_text().strip())
        os.kill(pid, 0)
    except (OSError, ValueError):
        _CORTEX_HTTP_PID_FILE.unlink(missing_ok=True)
        return False
    cmd = _http_forwarder_cmdline(pid)
    if "cortex_store.main:app" in cmd and str(_CORTEX_HTTP_PORT) in cmd:
        return True
    _CORTEX_HTTP_PID_FILE.unlink(missing_ok=True)
    return False


def _start_http_forwarder(root: Path, extra_env: dict[str, str]) -> str | None:
    """Second uvicorn on TCP for browser /control-tower (MCP keeps UDS)."""
    if _http_forwarder_running():
        pid = int(_CORTEX_HTTP_PID_FILE.read_text().strip())
        return (
            f"HTTP listener already on {_CORTEX_HTTP_HOST}:{_CORTEX_HTTP_PORT} "
            f"(PID {pid})"
        )
    venv_python = Path.home() / ".venvs" / "universal" / "bin" / "python"
    python = str(venv_python) if venv_python.exists() else "python3"
    env = build_service_env(root)
    env.update(extra_env)
    libs_path = str(root / "libs")
    env["PYTHONPATH"] = (
        f"{libs_path}:{env['PYTHONPATH']}" if env.get("PYTHONPATH") else libs_path
    )
    proc = subprocess.Popen(
        [
            python,
            "-m",
            "uvicorn",
            _CORTEX_APP_MODULE,
            "--host",
            _CORTEX_HTTP_HOST,
            "--port",
            str(_CORTEX_HTTP_PORT),
        ],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        cwd=str(root),
        env=env,
        start_new_session=True,
    )
    GATEWAY_DIR.mkdir(parents=True, exist_ok=True)
    _CORTEX_HTTP_PID_FILE.write_text(f"{proc.pid}\n")
    return (
        f"HTTP on http://{_CORTEX_HTTP_HOST}:{_CORTEX_HTTP_PORT}/control-tower "
        f"(PID {proc.pid})"
    )


def _stop_http_forwarder() -> None:
    if _CORTEX_HTTP_PID_FILE.exists():
        try:
            pid = int(_CORTEX_HTTP_PID_FILE.read_text().strip())
            os.kill(pid, 15)
        except (OSError, ValueError):
            pass
        _CORTEX_HTTP_PID_FILE.unlink(missing_ok=True)
    # Best-effort: rogue manual starts may leave uvicorn on the port while the
    # pid file points at a dead bash wrapper (observed 2026-06-01).
    try:
        subprocess.run(
            ["fuser", "-k", f"{_CORTEX_HTTP_PORT}/tcp"],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        pass


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
    # Two roots are wired here so the host process has the same view of
    # disk as the rest of the ecosystem:
    #
    #   CORTEX_FILES_ROOT — destination for session_close transcript
    #     writes. Must match the MCP container's /data/files bind mount
    #     (~/.gateway/mcp.yaml -> data_dir).
    #
    #   CURSOR_AGENT_TRANSCRIPTS_ROOT — source for the server-side
    #     verbatim assembly. Sandbox root validated by
    #     libs/cortex_store/transcript_assembly.resolve_jsonl_path. The
    #     default is the workspace's Cursor IDE agent-transcripts
    #     directory; operators override via env when running cortex-api
    #     against a different workspace.
    extra_env = _build_cortex_runtime_env(root)
    _logger.info(
        "Cortex API will read Cursor agent-transcripts from %s",
        extra_env["CURSOR_AGENT_TRANSCRIPTS_ROOT"],
    )

    def _on_uvicorn_ready(
        _socket_path: Path | None,
        _tcp_config: tuple[str, int] | None,
        _pid: int,
    ) -> None:
        msg = _start_http_forwarder(root, extra_env)
        if msg:
            _logger.info("%s", msg)

    base_msg = await _start_uvicorn_service(
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
        extra_env=extra_env or None,
        on_timeout_success=_on_uvicorn_ready,
    )
    if "already running" in base_msg.lower():
        fwd = _start_http_forwarder(root, extra_env)
        if fwd:
            return f"{base_msg} — {fwd}"
    return base_msg


async def stop_cortex_api(
    service_state: ServiceState,
    root: Path,  # noqa: ARG001
    kill_and_wait: Callable[..., Awaitable[str]],
) -> str:
    """Stop cortex-api gracefully."""
    _stop_http_forwarder()
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
