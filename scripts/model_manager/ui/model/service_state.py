"""Service state - tracks Gateway, Stargate, RAG, Cloud Proxy, and sidecar health."""

from __future__ import annotations

import logging
import re
import shutil
import socket
import subprocess
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

logger = logging.getLogger(__name__)


class ServiceStatus(StrEnum):
    RUNNING = "running"
    STOPPED = "stopped"
    UNHEALTHY = "unhealthy"
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


class ServiceState:
    """
    Checks health of Gateway (container), Stargate (host process), and RAG service.

    All methods are synchronous for simplicity; the TUI runs them
    via run_in_executor or Worker threads.
    """

    GATEWAY_PORT = 9998
    STARGATE_PORT = 9999
    RAG_PORT = 8100
    CLOUD_PROXY_PORT = 8200
    STARGATE_PID_FILE = Path.home() / ".gateway" / "stargate.pid"
    RAG_PID_FILE = Path.home() / ".gateway" / "rag.pid"
    CLOUD_PROXY_PID_FILE = Path.home() / ".gateway" / "cloud-proxy.pid"

    def __init__(self, workspace_root: Path) -> None:
        self._workspace_root = workspace_root

    def check_all(self) -> list[ServiceInfo]:
        return [
            self.check_gateway(),
            self.check_stargate(),
            self.check_rag(),
            self.check_cloud_proxy(),
        ]

    def check_rag(self) -> ServiceInfo:
        pid = self._read_pid(self.RAG_PID_FILE)
        if pid and self._pid_alive(pid):
            healthy = self._port_open(self.RAG_PORT)
            return ServiceInfo(
                name="RAG",
                status=ServiceStatus.RUNNING if healthy else ServiceStatus.UNHEALTHY,
                port=self.RAG_PORT,
                pid=pid,
                health_url=f"http://localhost:{self.RAG_PORT}/stats",
                detail=f"PID {pid}" + ("" if healthy else ", port not responding"),
            )
        if self._port_open(self.RAG_PORT):
            return ServiceInfo(
                name="RAG",
                status=ServiceStatus.RUNNING,
                port=self.RAG_PORT,
                detail="Port open (no PID file)",
            )
        return ServiceInfo(
            name="RAG",
            status=ServiceStatus.STOPPED,
            port=self.RAG_PORT,
        )

    def check_cloud_proxy(self) -> ServiceInfo:
        pid = self._read_pid(self.CLOUD_PROXY_PID_FILE)
        if pid and self._pid_alive(pid):
            healthy = self._port_open(self.CLOUD_PROXY_PORT)
            return ServiceInfo(
                name="Cloud Proxy",
                status=ServiceStatus.RUNNING if healthy else ServiceStatus.UNHEALTHY,
                port=self.CLOUD_PROXY_PORT,
                pid=pid,
                health_url=f"http://localhost:{self.CLOUD_PROXY_PORT}/health",
                detail=f"PID {pid}" + ("" if healthy else ", port not responding"),
            )
        if self._port_open(self.CLOUD_PROXY_PORT):
            return ServiceInfo(
                name="Cloud Proxy",
                status=ServiceStatus.RUNNING,
                port=self.CLOUD_PROXY_PORT,
                detail="Port open (no PID file)",
            )
        return ServiceInfo(
            name="Cloud Proxy",
            status=ServiceStatus.STOPPED,
            port=self.CLOUD_PROXY_PORT,
        )

    def check_gateway(self) -> ServiceInfo:
        container = self._check_container("edge-localhost")
        if container:
            return container
        return self._check_container_pattern("gateway")

    def check_stargate(self) -> ServiceInfo:
        pid = self._read_pid(self.STARGATE_PID_FILE)
        if pid and self._pid_alive(pid):
            healthy = self._port_open(self.STARGATE_PORT)
            return ServiceInfo(
                name="Stargate",
                status=ServiceStatus.RUNNING if healthy else ServiceStatus.UNHEALTHY,
                port=self.STARGATE_PORT,
                pid=pid,
                health_url=f"http://localhost:{self.STARGATE_PORT}/health",
                detail=f"PID {pid}" + ("" if healthy else ", port not responding"),
            )
        if self._port_open(self.STARGATE_PORT):
            return ServiceInfo(
                name="Stargate",
                status=ServiceStatus.RUNNING,
                port=self.STARGATE_PORT,
                detail="Port open (no PID file)",
            )
        return ServiceInfo(
            name="Stargate",
            status=ServiceStatus.STOPPED,
            port=self.STARGATE_PORT,
        )

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

    def _check_named_container(
        self, name: str, *, service_name: str = "Gateway"
    ) -> ServiceInfo | None:
        """Check a container by exact name. Returns None if not found."""
        if not shutil.which("docker"):
            return None
        try:
            result = subprocess.run(
                [
                    "docker",
                    "inspect",
                    "--format",
                    "{{.Name}}\t{{.State.Status}}",
                    name,
                ],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode == 0:
                parts = result.stdout.strip().split("\t", 1)
                if len(parts) == 2:
                    cname = parts[0].lstrip("/")
                    cstatus = parts[1]
                    running = cstatus == "running"
                    return ServiceInfo(
                        name=service_name,
                        status=ServiceStatus.RUNNING
                        if running
                        else ServiceStatus.UNHEALTHY,
                        container_name=cname,
                        detail=cstatus,
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
                    return ServiceInfo(
                        name="Gateway",
                        status=ServiceStatus.RUNNING
                        if running
                        else ServiceStatus.UNHEALTHY,
                        container_name=cname,
                        detail=cstatus,
                    )
        except (subprocess.TimeoutExpired, FileNotFoundError) as e:
            logger.warning("Docker check failed: %s", e)
        return None

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

    @staticmethod
    def _read_pid(path: Path) -> int | None:
        if not path.exists():
            return None
        try:
            return int(path.read_text().strip())
        except (ValueError, OSError):
            return None

    @staticmethod
    def _pid_alive(pid: int) -> bool:
        try:
            import os

            os.kill(pid, 0)
            return True
        except OSError:
            return False

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
