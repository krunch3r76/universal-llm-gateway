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
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from ..controller.service_config import (
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
        healthy = self._port_open(self.STARGATE_PORT)
        listener_pid = self._find_listener_pid(self.STARGATE_PORT) if healthy else None
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
                    + ("" if healthy else ", port not responding"),
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

    def check_cortex_api(self) -> ServiceInfo:
        """Check cortex-api container status using exact-name docker inspection helper."""
        info = self._check_named_container("cortex-api", service_name="Cortex")
        if info:
            return info
        return ServiceInfo(name="Cortex", status=ServiceStatus.STOPPED)

    def check_agent_bus(self) -> ServiceInfo:
        """Check agent-bus container status using exact-name docker inspection helper."""
        info = self._check_named_container("agent-bus", service_name="Agent Bus")
        if info:
            return info
        return ServiceInfo(name="Agent Bus", status=ServiceStatus.STOPPED)

    def check_event_service(self) -> ServiceInfo:
        """Check event service container status."""
        info = self._check_named_container("event-service", service_name="Events")
        if info:
            return info
        return ServiceInfo(name="Events", status=ServiceStatus.STOPPED)

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
