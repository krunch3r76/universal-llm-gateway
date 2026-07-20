"""Service state - tracks Gateway, Stargate, RAG, Cloud Proxy, and sidecar health."""

from __future__ import annotations

import logging
import os
import re
import shutil
import socket
import stat
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from ..controller.service_config import (
    cdp_ask_manage_state,
    cdp_ask_url_config,
    read_cloud_proxy_socket_path,
    read_rag_socket_path,
    read_rag_tcp_config,
)

logger = logging.getLogger(__name__)

_SERVICE_HEALTH_TIMEOUT = 2.0


class ServiceStatus(StrEnum):
    RUNNING = "running"
    STOPPED = "stopped"
    UNHEALTHY = "unhealthy"
    UNKNOWN = "unknown"
    NOT_ENABLED = "not_enabled"
    DISABLED = "disabled"


class ServiceOwnership(StrEnum):
    MANAGED = "managed"
    EXTERNAL = "external"
    UNKNOWN = "unknown"


@dataclass(slots=True, kw_only=True)
class ServiceInfo:
    name: str
    status: ServiceStatus
    port: int | None = None
    pid: int | None = None
    container_name: str | None = None
    health_url: str | None = None
    detail: str = ""
    ownership: ServiceOwnership = ServiceOwnership.UNKNOWN


class ServiceState:
    """
    Checks health of Gateway (container), Stargate (host process), and RAG service.

    All methods are synchronous for simplicity; the TUI runs them
    via run_in_executor or Worker threads.
    """

    GATEWAY_PORT: int = 9998
    STARGATE_PORT: int = 9999
    RAG_PORT: int = 8100
    CLOUD_PROXY_PORT: int = 8200  # TCP mode only; UDS mode uses socket
    STARGATE_PID_FILE: Path = Path.home() / ".gateway" / "stargate.pid"
    RAG_PID_FILE: Path = Path.home() / ".gateway" / "rag.pid"
    CLOUD_PROXY_PID_FILE: Path = Path.home() / ".gateway" / "cloud-proxy.pid"

    def __init__(self, workspace_root: Path) -> None:
        self._workspace_root: Path = workspace_root

    def check_all(self) -> list[ServiceInfo]:
        return [
            self.check_gateway(),
            self.check_stargate(),
            self.check_rag(),
            self.check_cloud_proxy(),
            self.check_event_service(),
            self.check_cortex_api(),
            self.check_agent_bus(),
            self.check_email_bridge(),
            self.check_git_integration_worker(),
            self.check_cdp_ask(),
        ]

    def check_rag(self) -> ServiceInfo:
        try:
            tcp_config = read_rag_tcp_config()
        except ValueError:
            tcp_config = None
        uds_mode = tcp_config is None
        socket_path = read_rag_socket_path()
        pid, pid_note = self._resolve_pid_file(self.RAG_PID_FILE)

        if uds_mode:
            return self._check_rag_uds(pid, socket_path, pid_note)
        return self._check_rag_tcp(pid, tcp_config, pid_note)

    def _check_rag_uds(
        self, pid: int | None, socket_path: Path, pid_note: str | None
    ) -> ServiceInfo:
        """UDS mode: PID + socket presence + readiness probe."""
        if not socket_path.exists():
            if pid is not None:
                return ServiceInfo(
                    name="RAG",
                    status=ServiceStatus.UNHEALTHY,
                    port=None,
                    pid=pid,
                    health_url=f"unix://{socket_path}/stats",
                    detail=self._with_note(f"PID {pid}, socket not ready", pid_note),
                )
            return ServiceInfo(
                name="RAG",
                status=ServiceStatus.STOPPED,
                port=None,
                detail=pid_note or "",
            )
        healthy = self._rag_probe_uds(socket_path)
        listener_pid = self._find_unix_listener_pid(socket_path) if healthy else None
        if listener_pid is not None and listener_pid != pid:
            self._write_pid_file(self.RAG_PID_FILE, listener_pid)
            pid = listener_pid
            pid_note = self._merge_notes(
                pid_note, "PID file refreshed from live socket"
            )
        if pid is not None:
            uptime = self._proc_uptime_str(pid)
            uptime_str = f" ({uptime})" if uptime else ""
            return ServiceInfo(
                name="RAG",
                status=ServiceStatus.RUNNING if healthy else ServiceStatus.UNHEALTHY,
                port=None,
                pid=pid,
                health_url=f"unix://{socket_path}/stats",
                detail=self._with_note(
                    f"PID {pid}{uptime_str}" + ("" if healthy else ", probe failed"),
                    pid_note,
                ),
            )
        if not healthy and self._unlink_stale_socket(socket_path):
            return ServiceInfo(
                name="RAG",
                status=ServiceStatus.STOPPED,
                port=None,
                detail=self._merge_notes(pid_note, "stale socket removed") or "",
            )
        return ServiceInfo(
            name="RAG",
            status=ServiceStatus.RUNNING if healthy else ServiceStatus.UNHEALTHY,
            port=None,
            detail=self._with_note(
                "Socket ready (PID file missing)"
                if healthy
                else "Socket ready, probe failed",
                pid_note,
            ),
        )

    def _rag_probe_uds(self, socket_path: Path) -> bool:
        """Perform readiness probe via UDS to /stats. Short timeout, fail closed."""
        from transport_utils import make_sync_client

        url = f"unix://{socket_path}"
        try:
            with make_sync_client(url, timeout=_SERVICE_HEALTH_TIMEOUT) as client:
                resp = client.get("/stats")
                return resp.status_code == 200
        except Exception:
            return False

    def _check_rag_tcp(
        self,
        pid: int | None,
        tcp_config: tuple[str, int],
        pid_note: str | None,
    ) -> ServiceInfo:
        """TCP mode: port-based + HTTP health."""
        host, port = tcp_config
        healthy = self._port_open(port, host)
        listener_pid = self._find_listener_pid(port) if healthy else None
        if listener_pid is not None and listener_pid != pid:
            self._write_pid_file(self.RAG_PID_FILE, listener_pid)
            pid = listener_pid
            pid_note = self._merge_notes(
                pid_note, "PID file refreshed from live listener"
            )
        if pid is not None:
            uptime = self._proc_uptime_str(pid)
            uptime_str = f" ({uptime})" if uptime else ""
            return ServiceInfo(
                name="RAG",
                status=ServiceStatus.RUNNING if healthy else ServiceStatus.UNHEALTHY,
                port=port,
                pid=pid,
                health_url=f"http://{host}:{port}/stats",
                detail=self._with_note(
                    f"PID {pid}{uptime_str}"
                    + ("" if healthy else ", port not responding"),
                    pid_note,
                ),
            )
        if healthy:
            return ServiceInfo(
                name="RAG",
                status=ServiceStatus.RUNNING,
                port=port,
                detail=self._with_note("Port open (PID file missing)", pid_note),
            )
        return ServiceInfo(
            name="RAG",
            status=ServiceStatus.STOPPED,
            port=port,
            detail=pid_note or "",
        )

    def check_cloud_proxy(self) -> ServiceInfo:
        try:
            from services.universal_cloud_proxy.config import load_config

            cfg = load_config(Path.home() / ".gateway" / "cloud-proxy.yaml")
            uds_mode = cfg.socket_path is not None
            host = cfg.host
            port = cfg.port
        except Exception:
            uds_mode = True
            host = "127.0.0.1"
            port = self.CLOUD_PROXY_PORT
        if uds_mode:
            return self._check_cloud_proxy_uds()
        return self._check_cloud_proxy_tcp(host=host, port=port)

    def _check_cloud_proxy_uds(self) -> ServiceInfo:
        """UDS mode: PID + socket + bounded readiness probe to /health."""
        pid, pid_note = self._resolve_pid_file(self.CLOUD_PROXY_PID_FILE)
        socket_path = read_cloud_proxy_socket_path()
        if not socket_path.exists():
            if pid is not None:
                return ServiceInfo(
                    name="Cloud Proxy",
                    status=ServiceStatus.UNHEALTHY,
                    port=None,
                    pid=pid,
                    health_url=f"unix://{socket_path}/health",
                    detail=self._with_note(f"PID {pid}, socket not ready", pid_note),
                )
            return ServiceInfo(
                name="Cloud Proxy",
                status=ServiceStatus.STOPPED,
                port=None,
                detail=pid_note or "",
            )
        healthy = self._cloud_proxy_probe_uds(socket_path)
        listener_pid = self._find_unix_listener_pid(socket_path) if healthy else None
        if listener_pid is not None and listener_pid != pid:
            self._write_pid_file(self.CLOUD_PROXY_PID_FILE, listener_pid)
            pid = listener_pid
            pid_note = self._merge_notes(
                pid_note, "PID file refreshed from live socket"
            )
        if pid is not None:
            uptime = self._proc_uptime_str(pid)
            uptime_str = f" ({uptime})" if uptime else ""
            return ServiceInfo(
                name="Cloud Proxy",
                status=ServiceStatus.RUNNING if healthy else ServiceStatus.UNHEALTHY,
                port=None,
                pid=pid,
                health_url=f"unix://{socket_path}/health",
                detail=self._with_note(
                    f"PID {pid}{uptime_str}" + ("" if healthy else ", probe failed"),
                    pid_note,
                ),
            )
        if not healthy and self._unlink_stale_socket(socket_path):
            return ServiceInfo(
                name="Cloud Proxy",
                status=ServiceStatus.STOPPED,
                port=None,
                detail=self._merge_notes(pid_note, "stale socket removed") or "",
            )
        return ServiceInfo(
            name="Cloud Proxy",
            status=ServiceStatus.RUNNING if healthy else ServiceStatus.UNHEALTHY,
            port=None,
            detail=self._with_note(
                "Socket ready (PID file missing)"
                if healthy
                else "Socket ready, probe failed",
                pid_note,
            ),
        )

    def _cloud_proxy_probe_uds(self, socket_path: Path) -> bool:
        """Probe /health via UDS. Short timeout, fail closed."""
        from transport_utils import make_sync_client

        try:
            with make_sync_client(
                f"unix://{socket_path}", timeout=_SERVICE_HEALTH_TIMEOUT
            ) as client:
                resp = client.get("/health")
                return resp.status_code == 200
        except Exception:
            return False

    def _check_cloud_proxy_tcp(self, *, host: str, port: int) -> ServiceInfo:
        """TCP mode: port-based + HTTP health."""
        pid, pid_note = self._resolve_pid_file(self.CLOUD_PROXY_PID_FILE)
        healthy = self._port_open(port, host)
        listener_pid = self._find_listener_pid(port) if healthy else None
        if listener_pid is not None and listener_pid != pid:
            self._write_pid_file(self.CLOUD_PROXY_PID_FILE, listener_pid)
            pid = listener_pid
            pid_note = self._merge_notes(
                pid_note, "PID file refreshed from live listener"
            )
        if pid is not None:
            uptime = self._proc_uptime_str(pid)
            uptime_str = f" ({uptime})" if uptime else ""
            return ServiceInfo(
                name="Cloud Proxy",
                status=ServiceStatus.RUNNING if healthy else ServiceStatus.UNHEALTHY,
                port=port,
                pid=pid,
                health_url=f"http://{host}:{port}/health",
                detail=self._with_note(
                    f"PID {pid}{uptime_str}"
                    + ("" if healthy else ", port not responding"),
                    pid_note,
                ),
            )
        if healthy:
            return ServiceInfo(
                name="Cloud Proxy",
                status=ServiceStatus.RUNNING,
                port=port,
                detail=self._with_note("Port open (PID file missing)", pid_note),
            )
        return ServiceInfo(
            name="Cloud Proxy",
            status=ServiceStatus.STOPPED,
            port=port,
            detail=pid_note or "",
        )

    def check_gateway(self) -> ServiceInfo:
        container = self._check_container("edge-localhost")
        if container:
            return container
        return self._check_container_pattern("gateway")

    def check_stargate(self) -> ServiceInfo:
        pid, pid_note = self._resolve_pid_file(self.STARGATE_PID_FILE)
        port_open = self._port_open(self.STARGATE_PORT)
        listener_pid = (
            self._find_listener_pid(self.STARGATE_PORT) if port_open else None
        )
        healthy, health_note = (
            self._stargate_probe_tcp(host="127.0.0.1", port=self.STARGATE_PORT)
            if port_open
            else (False, None)
        )
        if listener_pid is not None and listener_pid != pid:
            self._write_pid_file(self.STARGATE_PID_FILE, listener_pid)
            pid = listener_pid
            pid_note = self._merge_notes(
                pid_note, "PID file refreshed from live listener"
            )
        if pid is not None:
            uptime = self._proc_uptime_str(pid)
            uptime_str = f" ({uptime})" if uptime else ""
            return ServiceInfo(
                name="Stargate",
                status=ServiceStatus.RUNNING if healthy else ServiceStatus.UNHEALTHY,
                port=self.STARGATE_PORT,
                pid=pid,
                health_url=f"http://localhost:{self.STARGATE_PORT}/health",
                detail=self._with_note(
                    f"PID {pid}{uptime_str}"
                    + ("" if healthy else f", {health_note or 'port not responding'}"),
                    pid_note,
                ),
            )
        if healthy:
            return ServiceInfo(
                name="Stargate",
                status=ServiceStatus.RUNNING,
                port=self.STARGATE_PORT,
                detail=self._with_note("Port open (PID file missing)", pid_note),
            )
        return ServiceInfo(
            name="Stargate",
            status=ServiceStatus.STOPPED,
            port=self.STARGATE_PORT,
            detail=pid_note or "",
        )

    def _stargate_probe_tcp(self, *, host: str, port: int) -> tuple[bool, str | None]:
        """Probe Stargate /health and honor pipeline readiness when present."""
        from transport_utils import make_sync_client

        try:
            with make_sync_client(
                f"http://{host}:{port}", timeout=_SERVICE_HEALTH_TIMEOUT
            ) as client:
                resp = client.get("/health")
                if resp.status_code != 200:
                    return (False, f"/health returned {resp.status_code}")
                try:
                    payload = resp.json()
                except ValueError:
                    return (True, None)
                if (
                    isinstance(payload, dict)
                    and payload.get("pipeline_system_ready") is False
                ):
                    count = payload.get("pipeline_count")
                    suffix = f" (count={count})" if count is not None else ""
                    return (False, f"pipeline system not ready{suffix}")
                return (True, None)
        except Exception as exc:
            return (False, f"health probe failed: {type(exc).__name__}")

    def check_sidecar(self) -> ServiceInfo:
        """Check pipeline-tools sidecar container status."""
        from ..controller.sidecar_ctl import SIDECAR_NAME

        info = self._check_named_container(SIDECAR_NAME, service_name="Sidecar")
        if info:
            return info
        return ServiceInfo(
            name="Sidecar",
            status=ServiceStatus.STOPPED,
        )

    def check_mcp(self) -> ServiceInfo:
        """Check MCP server container status."""
        info = self._check_named_container("mcp-server", service_name="MCP")
        if info:
            return info
        return ServiceInfo(name="MCP", status=ServiceStatus.STOPPED)

    CORTEX_API_PID_FILE: Path = Path.home() / ".gateway" / "cortex-api.pid"
    CORTEX_API_SOCK: Path = Path(
        os.environ.get("CORTEX_API_SOCK", "/tmp/universal-protocol/cortex-api.sock")
    )

    AGENT_BUS_PID_FILE: Path = Path.home() / ".gateway" / "agent-bus.pid"
    AGENT_BUS_SOCK: Path = Path(
        os.environ.get("AGENT_BUS_SOCK", "/tmp/universal-protocol/agent-bus.sock")
    )
    EMAIL_BRIDGE_PID_FILE: Path = Path.home() / ".gateway" / "email-bridge.pid"
    EMAIL_BRIDGE_SOCK: Path = Path(
        os.environ.get("EMAIL_BRIDGE_SOCK", "/tmp/universal-protocol/email-bridge.sock")
    )

    def check_cortex_api(self) -> ServiceInfo:
        """Check cortex-api status via PID file + UDS health probe."""
        return self._check_uds_service(
            name="Cortex",
            pid_file=self.CORTEX_API_PID_FILE,
            socket_path=self.CORTEX_API_SOCK,
            health_endpoint="/health",
            managed_pid_predicate=lambda pid: self._pid_cmdline_contains(
                pid, "cortex_store.main:app", "--uds"
            ),
        )

    def check_agent_bus(self) -> ServiceInfo:
        """Check agent-bus status via PID file + UDS health probe."""
        return self._check_uds_service(
            name="Agent Bus",
            pid_file=self.AGENT_BUS_PID_FILE,
            socket_path=self.AGENT_BUS_SOCK,
            health_endpoint="/health",
            managed_pid_predicate=lambda pid: self._pid_cmdline_contains(
                pid, "agent_bus_store.server:app", "--uds"
            ),
        )

    def check_email_bridge(self) -> ServiceInfo:
        """Check email-bridge status via PID file + UDS health probe."""
        return self._check_uds_service(
            name="Email Bridge",
            pid_file=self.EMAIL_BRIDGE_PID_FILE,
            socket_path=self.EMAIL_BRIDGE_SOCK,
            health_endpoint="/health",
            managed_pid_predicate=lambda pid: self._pid_cmdline_contains(
                pid, "src.main:app", "email-bridge.sock"
            ),
        )

    GIT_INTEGRATION_WORKER_PID_FILE: Path = (
        Path.home() / ".gateway" / "git-integration-worker.pid"
    )
    GIT_INTEGRATION_WORKER_HOST: str = os.environ.get(
        "GIT_INTEGRATION_WORKER_HOST", "127.0.0.1"
    )
    GIT_INTEGRATION_WORKER_PORT: int = int(
        os.environ.get("GIT_INTEGRATION_WORKER_PORT", "8091")
    )

    CDP_ASK_PID_FILE: Path = Path.home() / ".gateway" / "cdp-ask.pid"

    def check_cdp_ask(self) -> ServiceInfo:
        """Check cdp-ask satellite status via TCP /health when URL is configured."""
        manage_state = cdp_ask_manage_state()
        if manage_state == "not_enabled":
            return ServiceInfo(
                name="cdp-ask",
                status=ServiceStatus.NOT_ENABLED,
                detail="PROJECT_ASK_URL unset",
            )

        url_cfg = cdp_ask_url_config()
        if url_cfg is None:
            return ServiceInfo(
                name="cdp-ask",
                status=ServiceStatus.NOT_ENABLED,
                detail="PROJECT_ASK_URL unset",
            )
        host, port, base_url = url_cfg
        health_url = f"{base_url}/health"
        probe_host = "127.0.0.1" if host in {"localhost", "127.0.0.1", "::1"} else host

        if manage_state == "disabled":
            healthy, health_detail = self._cdp_ask_health_observation(probe_host, port)
            observed = "process running" if healthy else "process stopped/unhealthy"
            detail = f"disabled; {observed}"
            if health_detail:
                detail = f"{detail} ({health_detail})"
            return ServiceInfo(
                name="cdp-ask",
                status=ServiceStatus.DISABLED,
                port=port,
                health_url=health_url,
                detail=detail,
                ownership=ServiceOwnership.UNKNOWN,
            )

        pid, pid_note = self._resolve_pid_file(self.CDP_ASK_PID_FILE)
        port_open = self._port_open(port, probe_host)
        listener_pid = self._find_listener_pid(port) if port_open else None
        if listener_pid is not None and listener_pid != pid:
            self._write_pid_file(self.CDP_ASK_PID_FILE, listener_pid)
            pid = listener_pid
            pid_note = self._merge_notes(
                pid_note, "PID file refreshed from live listener"
            )
        healthy, health_detail = (
            self._cdp_ask_health_observation(probe_host, port)
            if port_open
            else (False, None)
        )
        if pid is not None:
            uptime = self._proc_uptime_str(pid)
            uptime_str = f" ({uptime})" if uptime else ""
            detail = self._with_note(
                f"PID {pid}{uptime_str}"
                + ("" if healthy else f", {health_detail or 'health probe failed'}"),
                pid_note,
            )
            if healthy and health_detail:
                detail = self._with_note(detail, health_detail)
            return ServiceInfo(
                name="cdp-ask",
                status=ServiceStatus.RUNNING if healthy else ServiceStatus.UNHEALTHY,
                port=port,
                pid=pid,
                health_url=health_url,
                detail=detail,
                ownership=ServiceOwnership.MANAGED,
            )
        if healthy:
            detail = self._with_note(
                self._merge_notes("Port open (PID file missing)", health_detail) or "",
                pid_note,
            )
            return ServiceInfo(
                name="cdp-ask",
                status=ServiceStatus.RUNNING,
                port=port,
                health_url=health_url,
                detail=detail or "Port open (PID file missing)",
                ownership=ServiceOwnership.MANAGED,
            )
        return ServiceInfo(
            name="cdp-ask",
            status=ServiceStatus.STOPPED,
            port=port,
            health_url=health_url,
            detail=pid_note or "",
            ownership=ServiceOwnership.MANAGED,
        )

    def _cdp_ask_health_observation(
        self, host: str, port: int
    ) -> tuple[bool, str | None]:
        """Probe ``GET /health``; return healthy flag and optional detail."""
        import http.client
        import json as _json

        try:
            conn = http.client.HTTPConnection(host, port, timeout=_SERVICE_HEALTH_TIMEOUT)
            try:
                conn.request("GET", "/health")
                resp = conn.getresponse()
                if resp.status != 200:
                    return False, f"/health returned {resp.status}"
                body = _json.loads(resp.read().decode("utf-8", errors="replace"))
                if body.get("status") != "ok":
                    return False, f"status={body.get('status')}"
                hygiene = body.get("registry_hygiene")
                if hygiene:
                    return True, f"registry_hygiene={hygiene}"
                return True, None
            finally:
                conn.close()
        except Exception as exc:
            return False, f"health probe failed: {type(exc).__name__}"

    def check_git_integration_worker(self) -> ServiceInfo:
        """Check git-integration-worker status via PID file + TCP /health probe."""
        pid, pid_note = self._resolve_pid_file(self.GIT_INTEGRATION_WORKER_PID_FILE)
        host = self.GIT_INTEGRATION_WORKER_HOST
        port = self.GIT_INTEGRATION_WORKER_PORT
        port_open = self._port_open(port, host)
        listener_pid = self._find_listener_pid(port) if port_open else None
        if listener_pid is not None and listener_pid != pid:
            self._write_pid_file(self.GIT_INTEGRATION_WORKER_PID_FILE, listener_pid)
            pid = listener_pid
            pid_note = self._merge_notes(
                pid_note, "PID file refreshed from live listener"
            )
        healthy = (
            self._git_integration_worker_probe_http(host, port) if port_open else False
        )
        health_url = f"http://{host}:{port}/health"
        if pid is not None:
            uptime = self._proc_uptime_str(pid)
            uptime_str = f" ({uptime})" if uptime else ""
            return ServiceInfo(
                name="git-integration-worker",
                status=ServiceStatus.RUNNING if healthy else ServiceStatus.UNHEALTHY,
                port=port,
                pid=pid,
                health_url=health_url,
                detail=self._with_note(
                    f"PID {pid}{uptime_str}"
                    + ("" if healthy else ", health probe failed"),
                    pid_note,
                ),
            )
        if healthy:
            return ServiceInfo(
                name="git-integration-worker",
                status=ServiceStatus.RUNNING,
                port=port,
                health_url=health_url,
                detail=self._with_note("Port open (PID file missing)", pid_note),
            )
        return ServiceInfo(
            name="git-integration-worker",
            status=ServiceStatus.STOPPED,
            port=port,
            detail=pid_note or "",
        )

    def _git_integration_worker_probe_http(self, host: str, port: int) -> bool:
        """Probe ``GET /health``; healthy when status is ``ok``."""
        import http.client
        import json as _json

        try:
            conn = http.client.HTTPConnection(
                host, port, timeout=_SERVICE_HEALTH_TIMEOUT
            )
            try:
                conn.request("GET", "/health")
                resp = conn.getresponse()
                if resp.status != 200:
                    return False
                body = _json.loads(resp.read().decode("utf-8", errors="replace"))
                return body.get("status") == "ok"
            finally:
                conn.close()
        except Exception:
            return False

    EVENT_SERVICE_PID_FILE: Path = Path.home() / ".gateway" / "event-service.pid"
    EVENT_SERVICE_QUERY_SOCK: Path = Path(
        os.environ.get("EVENTS_QUERY_SOCK", "/tmp/universal-protocol/events-query.sock")
    )

    def check_event_service(self) -> ServiceInfo:
        """Check event service status via PID file + UDS health probe."""
        return self._check_uds_service(
            name="Events",
            pid_file=self.EVENT_SERVICE_PID_FILE,
            socket_path=self.EVENT_SERVICE_QUERY_SOCK,
            health_endpoint="/health",
            managed_pid_predicate=lambda pid: self._pid_cmdline_contains(
                pid, "event_store", "serve"
            ),
        )

    def _event_service_probe_uds(self, socket_path: Path) -> bool:
        """Probe /health via UDS on the event-service query socket."""
        return self._probe_uds_health(socket_path, "/health")

    def _probe_uds_health(self, socket_path: Path, endpoint: str = "/health") -> bool:
        """Probe an endpoint via UDS. Short timeout, fail closed."""
        from transport_utils import make_sync_client

        try:
            with make_sync_client(
                f"unix://{socket_path}", timeout=_SERVICE_HEALTH_TIMEOUT
            ) as client:
                resp = client.get(endpoint)
                return resp.status_code == 200
        except Exception:
            return False

    def _check_uds_service(
        self,
        *,
        name: str,
        pid_file: Path,
        socket_path: Path,
        health_endpoint: str = "/health",
        managed_pid_predicate: Callable[[int], bool] | None = None,
    ) -> ServiceInfo:
        """Generic UDS service health check: PID file + socket + health probe."""
        pid, pid_note = self._resolve_pid_file(pid_file)
        managed_pid = (
            pid
            if pid is not None and self._pid_is_managed(pid, managed_pid_predicate)
            else None
        )

        if not socket_path.exists():
            if managed_pid is not None:
                return ServiceInfo(
                    name=name,
                    status=ServiceStatus.UNHEALTHY,
                    pid=managed_pid,
                    health_url=f"unix://{socket_path}{health_endpoint}",
                    detail=self._with_note(
                        f"PID {managed_pid}, socket not ready", pid_note
                    ),
                    ownership=ServiceOwnership.MANAGED,
                )
            return ServiceInfo(
                name=name,
                status=ServiceStatus.STOPPED,
                detail=pid_note or "",
            )
        healthy = self._probe_uds_health(socket_path, health_endpoint)
        listener_pid = self._find_unix_listener_pid(socket_path) if healthy else None
        listener_is_managed = self._pid_is_managed(listener_pid, managed_pid_predicate)
        if (
            listener_is_managed
            and listener_pid is not None
            and listener_pid != managed_pid
        ):
            self._write_pid_file(pid_file, listener_pid)
            managed_pid = listener_pid
            pid_note = self._merge_notes(
                pid_note, "PID file refreshed from live socket"
            )
        if managed_pid is not None:
            uptime = self._proc_uptime_str(managed_pid)
            uptime_str = f" ({uptime})" if uptime else ""
            return ServiceInfo(
                name=name,
                status=ServiceStatus.RUNNING if healthy else ServiceStatus.UNHEALTHY,
                pid=managed_pid,
                health_url=f"unix://{socket_path}{health_endpoint}",
                detail=self._with_note(
                    f"PID {managed_pid}{uptime_str}"
                    + ("" if healthy else ", probe failed"),
                    pid_note,
                ),
                ownership=ServiceOwnership.MANAGED,
            )
        if not healthy and self._unlink_stale_socket(socket_path):
            return ServiceInfo(
                name=name,
                status=ServiceStatus.STOPPED,
                detail=self._merge_notes(pid_note, "stale socket removed") or "",
            )
        if listener_pid is not None:
            uptime = self._proc_uptime_str(listener_pid)
            uptime_str = f" ({uptime})" if uptime else ""
            return ServiceInfo(
                name=name,
                status=ServiceStatus.RUNNING if healthy else ServiceStatus.UNHEALTHY,
                pid=listener_pid,
                health_url=f"unix://{socket_path}{health_endpoint}",
                detail=self._with_note(
                    f"PID {listener_pid}{uptime_str}, externally managed"
                    + ("" if healthy else ", probe failed"),
                    pid_note,
                ),
                ownership=ServiceOwnership.EXTERNAL,
            )
        return ServiceInfo(
            name=name,
            status=ServiceStatus.RUNNING if healthy else ServiceStatus.UNHEALTHY,
            detail=self._with_note(
                "Socket ready (PID file missing)"
                if healthy
                else "Socket ready, probe failed",
                pid_note,
            ),
        )

    def _check_named_container(
        self, name: str, *, service_name: str = "Gateway"
    ) -> ServiceInfo | None:
        """Check a container by exact name using docker ps (includes uptime in Status).

        Uses docker ps --filter name=... which returns the same 'Up X minutes (healthy)'
        string as _check_container, giving uptime visibility at no extra cost.
        Also appends the last start time (wall clock) so rebuilds are visible.
        """
        if not shutil.which("docker"):
            return None
        try:
            result = subprocess.run(
                [
                    "docker",
                    "ps",
                    "-a",
                    "--filter",
                    f"name=^/{name}$",
                    "--format",
                    "{{.Names}}\t{{.Status}}",
                ],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode == 0:
                for line in result.stdout.strip().splitlines():
                    parts = line.split("\t", 1)
                    if len(parts) == 2 and parts[0] == name:
                        cname, cstatus = parts
                        running = cstatus.startswith("Up")
                        built = self._container_image_built_at(name)
                        detail = f"{cstatus}, built {built}" if built else cstatus
                        return ServiceInfo(
                            name=service_name,
                            status=ServiceStatus.RUNNING
                            if running
                            else ServiceStatus.UNHEALTHY,
                            container_name=cname,
                            detail=detail,
                        )
        except (subprocess.TimeoutExpired, FileNotFoundError) as e:
            logger.warning("Docker check failed for %s: %s", name, e)
        return None

    def _check_container(self, name: str) -> ServiceInfo | None:
        if not shutil.which("docker"):
            return None
        try:
            result = subprocess.run(
                [
                    "docker",
                    "ps",
                    "--filter",
                    f"name={name}",
                    "--format",
                    "{{.Names}}\t{{.Status}}",
                ],
                capture_output=True,
                text=True,
                timeout=5,
            )
            for line in result.stdout.strip().splitlines():
                parts = line.split("\t", 1)
                if len(parts) == 2:
                    cname, cstatus = parts
                    running = "Up" in cstatus
                    built = self._container_image_built_at(cname)
                    detail = f"{cstatus}, built {built}" if built else cstatus
                    return ServiceInfo(
                        name="Gateway",
                        status=ServiceStatus.RUNNING
                        if running
                        else ServiceStatus.UNHEALTHY,
                        container_name=cname,
                        detail=detail,
                    )
        except (subprocess.TimeoutExpired, FileNotFoundError) as e:
            logger.warning("Docker check failed: %s", e)
        return None

    @staticmethod
    def _container_image_built_at(name: str) -> str:
        """Return local HH:MM:SS when the container's image was built, or ''.

        Two inspect calls: container → image SHA, image SHA → Created timestamp.
        Returns '' on any error so callers degrade gracefully.
        """
        from datetime import datetime

        try:
            # Step 1: get image SHA from container
            r1 = subprocess.run(
                ["docker", "inspect", "--format", "{{.Image}}", name],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if r1.returncode != 0:
                return ""
            image_sha = r1.stdout.strip()
            if not image_sha:
                return ""

            # Step 2: get Created timestamp from image
            r2 = subprocess.run(
                ["docker", "inspect", "--format", "{{.Created}}", image_sha],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if r2.returncode != 0:
                return ""
            raw = r2.stdout.strip()
            dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            if dt.year < 2000:
                return ""
            return dt.astimezone().strftime("%H:%M:%S")
        except Exception:
            return ""

    def _check_container_pattern(self, pattern: str) -> ServiceInfo:
        info = self._check_container(pattern)
        if info:
            return info
        if self._port_open(self.GATEWAY_PORT):
            return ServiceInfo(
                name="Gateway",
                status=ServiceStatus.RUNNING,
                port=self.GATEWAY_PORT,
                detail="Port open (no container found)",
            )
        return ServiceInfo(
            name="Gateway",
            status=ServiceStatus.STOPPED,
            port=self.GATEWAY_PORT,
        )

    @staticmethod
    def _port_open(port: int, host: str = "127.0.0.1") -> bool:
        try:
            with socket.create_connection((host, port), timeout=2):
                return True
        except OSError:
            return False

    def _resolve_pid_file(self, path: Path) -> tuple[int | None, str | None]:
        if not path.exists():
            return None, None
        try:
            pid = int(path.read_text().strip())
        except ValueError:
            logger.warning("Invalid PID contents in %s", path)
            _ = self._unlink_path(path)
            return None, "invalid PID file removed"
        except OSError as exc:
            logger.warning("Failed reading PID file %s: %s", path, exc)
            return None, None
        if self._pid_alive(pid):
            return pid, None
        logger.info("Removing stale PID file %s for dead PID %d", path, pid)
        if self._unlink_path(path):
            return None, f"stale PID file removed (PID {pid})"
        return None, f"stale PID file detected (PID {pid})"

    @staticmethod
    def _pid_alive(pid: int) -> bool:
        try:
            os.kill(pid, 0)
            return True
        except ProcessLookupError:
            return False
        except PermissionError:
            # Process exists but is not signalable by current user.
            return True
        except OSError:
            return False

    @staticmethod
    def _proc_uptime_str(pid: int) -> str:
        """Return human-readable uptime for a PID using /proc/{pid}/stat.

        ∀ pid alive: returns e.g. '2h 15m', '45m 3s', '12s'.
        Falls back to '' on any error (permission denied, proc gone, etc.).
        """
        try:
            with open(f"/proc/{pid}/stat") as f:
                stat = f.read().split()
            starttime_ticks = int(stat[21])
            clk_tck = os.sysconf("SC_CLK_TCK")
            with open("/proc/uptime") as f:
                uptime_s = float(f.read().split()[0])
            boot_time = time.time() - uptime_s
            elapsed = time.time() - (boot_time + starttime_ticks / clk_tck)
            if elapsed < 0:
                return ""
            hours = int(elapsed // 3600)
            minutes = int((elapsed % 3600) // 60)
            seconds = int(elapsed % 60)
            if hours > 0:
                return f"{hours}h {minutes}m"
            if minutes > 0:
                return f"{minutes}m {seconds}s"
            return f"{seconds}s"
        except Exception:
            return ""

    @staticmethod
    def _find_listener_pid(port: int) -> int | None:
        """Find PID of the process listening on port using ss(8).

        Preferred over lsof: faster, part of iproute2 (universally available
        on Linux). Returns None if ss is unavailable or the port has no
        identifiable listener (e.g. owned by another user).
        """
        try:
            result = subprocess.run(
                ["ss", "-Htlnp", f"sport = :{port}"],
                capture_output=True,
                text=True,
                timeout=3,
            )
            m = re.search(r"pid=(\d+)", result.stdout)
            if m:
                return int(m.group(1))
        except (FileNotFoundError, subprocess.TimeoutExpired) as e:
            logger.warning("ss lookup failed for port %d: %s", port, e)
        return None

    @staticmethod
    def _find_unix_listener_pid(socket_path: Path) -> int | None:
        """Find PID of the process listening on a Unix socket path using ss(8)."""
        try:
            result = subprocess.run(
                ["ss", "-Hxlpn"],
                capture_output=True,
                text=True,
                timeout=3,
            )
            for line in result.stdout.splitlines():
                if str(socket_path) not in line:
                    continue
                match = re.search(r"pid=(\d+)", line)
                if match:
                    return int(match.group(1))
        except (FileNotFoundError, subprocess.TimeoutExpired) as e:
            logger.warning("ss lookup failed for socket %s: %s", socket_path, e)
        return None

    @staticmethod
    def _pid_cmdline_contains(pid: int, *needles: str) -> bool:
        try:
            cmdline = Path(f"/proc/{pid}/cmdline").read_bytes().decode(errors="replace")
        except OSError:
            return False
        return all(needle in cmdline for needle in needles)

    @staticmethod
    def _pid_is_managed(
        pid: int | None, predicate: Callable[[int], bool] | None
    ) -> bool:
        if pid is None:
            return False
        if predicate is None:
            return True
        return predicate(pid)

    @staticmethod
    def _unlink_path(path: Path) -> bool:
        try:
            path.unlink(missing_ok=True)
            return True
        except OSError as exc:
            logger.warning("Failed to remove %s: %s", path, exc)
            return False

    def _unlink_stale_socket(self, socket_path: Path) -> bool:
        """Remove a stale Unix socket when connect() proves no listener exists."""
        if not socket_path.exists():
            return False
        try:
            mode = socket_path.lstat().st_mode
            if not stat.S_ISSOCK(mode):
                logger.warning("Path %s is not a socket, refusing cleanup", socket_path)
                return False
        except OSError as exc:
            logger.warning("Could not stat socket %s: %s", socket_path, exc)
            return False

        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as probe:
                probe.settimeout(0.5)
                probe.connect(str(socket_path))
            return False
        except ConnectionRefusedError:
            logger.info(
                "Removing stale socket %s after connection refused", socket_path
            )
            return self._unlink_path(socket_path)
        except OSError as exc:
            logger.warning("Socket probe failed for %s: %s", socket_path, exc)
            return False

    @staticmethod
    def _merge_notes(*notes: str | None) -> str | None:
        parts = [note for note in notes if note]
        if not parts:
            return None
        return "; ".join(parts)

    def _with_note(self, detail: str, note: str | None) -> str:
        merged = self._merge_notes(detail, note)
        return merged or detail

    @staticmethod
    def _write_pid_file(path: Path, pid: int) -> None:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            _ = path.write_text(f"{pid}\n")
        except OSError as exc:
            logger.warning("Failed to write PID file %s: %s", path, exc)
