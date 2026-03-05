"""Cloud Proxy service lifecycle — start, stop, PID/socket management.

Part of the model_manager UI controller. Manages the Cloud Proxy uvicorn
process with UDS or TCP transport, lock files, and stale socket cleanup.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from pathlib import Path

from ...model.service_state import ServiceState
from ..service_config import (
    CLOUD_PROXY_SOCKET_PATH_DEFAULT,
    GATEWAY_DIR,
    ensure_cloud_proxy_config,
    read_cloud_proxy_socket_path,
    write_cloud_proxy_url_to_stargate,
)
from .uvicorn_service import _start_uvicorn_service, _stop_uvicorn_service

_CLOUD_PROXY_APP_MODULE = "services.universal_cloud_proxy.cloud_proxy:app"
_CLOUD_PROXY_LOCK_FILE = GATEWAY_DIR / "cloud-proxy.lock"


def _on_cloud_proxy_timeout_success(
    socket_path: Path | None,
    tcp_config: tuple[str, int] | None,
    pid: int,
) -> None:
    """Write cloud proxy URL to stargate config after successful start."""
    if tcp_config is not None:
        host, port = tcp_config
        proxy_url = f"http://{host}:{port}"
    elif socket_path is not None:
        proxy_url = f"unix://{socket_path}"
    else:
        return
    write_cloud_proxy_url_to_stargate(proxy_url)


async def start_cloud_proxy(
    service_state: ServiceState,
    root: Path,
    kill_and_wait: Callable[..., Awaitable[str]],
) -> str:
    """Start Cloud Proxy service as host process via uvicorn.

    UDS mode (default): socket at /tmp/universal-protocol/cloud-proxy.sock
    TCP mode (deprecated): host:port from cloud-proxy.yaml
    """
    ensure_cloud_proxy_config()
    try:
        from services.universal_cloud_proxy.config import load_config

        cfg = load_config(GATEWAY_DIR / "cloud-proxy.yaml")
        uds_mode = cfg.socket_path is not None
        socket_path = read_cloud_proxy_socket_path() if uds_mode else None
        tcp_config: tuple[str, int] | None = (
            (cfg.host, cfg.port) if not uds_mode else None
        )
    except Exception as e:
        return f"Cloud proxy config error: {e}"

    pid_file = GATEWAY_DIR / "cloud-proxy.pid"

    return await _start_uvicorn_service(
        service_state=service_state,
        root=root,
        app_module=_CLOUD_PROXY_APP_MODULE,
        pid_file=pid_file,
        lock_file=_CLOUD_PROXY_LOCK_FILE,
        service_name="Cloud Proxy",
        socket_path=socket_path,
        tcp_config=tcp_config,
        log_dir=Path("/tmp/logs/universal-cloud-proxy"),
        log_filename="cloud-proxy.log",
        on_timeout_success=_on_cloud_proxy_timeout_success,
    )


async def stop_cloud_proxy(
    service_state: ServiceState,
    root: Path,
    kill_and_wait: Callable[..., Awaitable[str]],
) -> str:
    """Stop Cloud Proxy service gracefully."""
    pid_file = GATEWAY_DIR / "cloud-proxy.pid"
    try:
        from services.universal_cloud_proxy.config import load_config

        cfg = load_config(GATEWAY_DIR / "cloud-proxy.yaml")
        uds_mode = cfg.socket_path is not None
        socket_path = read_cloud_proxy_socket_path() if uds_mode else None
        tcp_config: tuple[str, int] | None = (
            (cfg.host, cfg.port) if not uds_mode else None
        )
    except Exception:
        uds_mode = True
        socket_path = Path(CLOUD_PROXY_SOCKET_PATH_DEFAULT)
        tcp_config = None

    return await _stop_uvicorn_service(
        service_state=service_state,
        kill_and_wait=kill_and_wait,
        pid_file=pid_file,
        lock_file=_CLOUD_PROXY_LOCK_FILE,
        service_name="Cloud Proxy",
        socket_path=socket_path,
        tcp_config=tcp_config,
        app_module=_CLOUD_PROXY_APP_MODULE,
    )
