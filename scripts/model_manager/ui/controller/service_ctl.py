"""Service controller - build, start, stop Gateway, Stargate, RAG, Cloud Proxy, and sidecar."""

from __future__ import annotations

import asyncio
import logging
import os
import signal
from collections.abc import AsyncIterator
from pathlib import Path

from ..model.build_state import BuildState, ImageInfo
from ..model.service_state import ServiceState
from .service_config import (
    GATEWAY_DIR,
    NODES_DIR,
    build_service_env,
    ensure_bind_mount_dirs,
    ensure_cloud_proxy_config,
    ensure_node_env,
    ensure_socket_dir,
    ensure_stargate_config,
    load_env_file,
    read_cloud_proxy_host,
    read_cloud_proxy_port,
)
from .sidecar_ctl import SidecarController

logger = logging.getLogger(__name__)

_PGID_KILL_TIMEOUT = 5


class ServiceController:
    """
    Orchestrates Docker builds and service lifecycle.

    Delegates to existing shell scripts; does not reimplement their logic.
    """

    def __init__(self, workspace_root: Path) -> None:
        self._root = workspace_root
        self._build_state = BuildState()
        self._service_state = ServiceState(workspace_root)
        self._sidecar = SidecarController(workspace_root)
        self._build_process: asyncio.subprocess.Process | None = None

    @property
    def service_state(self) -> ServiceState:
        return self._service_state

    def check_model_path_ownership(self) -> str | None:
        """Return warning if MODEL_PATH is root-owned, None if OK."""
        node_env = load_env_file(NODES_DIR / "localhost.env")
        model_path = Path(
            node_env.get("MODEL_PATH", str(Path.home() / ".models"))
        ).expanduser()
        if model_path.exists() and model_path.stat().st_uid == 0 and os.getuid() != 0:
            uid, gid = os.getuid(), os.getgid()
            return (
                f"{model_path} is owned by root (Docker bind mount artifact).\n"
                f"Fix: sudo chown -R {uid}:{gid} {model_path}"
            )
        return None

    @property
    def build_running(self) -> bool:
        return self._build_process is not None

    def check_image(self) -> ImageInfo:
        return self._build_state.check_image()

    async def cancel_build(self) -> str:
        """Kill the running build process group."""
        proc = self._build_process
        if proc is None or proc.returncode is not None:
            return "No build in progress."
        try:
            os.killpg(proc.pid, signal.SIGTERM)
            try:
                await asyncio.wait_for(proc.wait(), timeout=_PGID_KILL_TIMEOUT)
            except TimeoutError:
                os.killpg(proc.pid, signal.SIGKILL)
                await proc.wait()
        except (ProcessLookupError, PermissionError) as e:
            logger.warning("cancel_build: %s", e)
        return "Build cancelled."

    async def build_image(
        self,
        *,
        scope: str = "all",
        no_cache: bool = False,
        gpu_native: bool = True,
        cpu_native: bool = True,
    ) -> AsyncIterator[str]:
        """
        Build Docker GPU image via build-gpu.sh, yielding log lines.

        Yields lines from stdout/stderr as they appear.
        """
        script = self._root / "docker" / "scripts" / "build" / "build-gpu.sh"
        if not script.exists():
            yield f"ERROR: Build script not found: {script}"
            return

        args = [str(script)]
        if cpu_native:
            args.append("--cpu-native")
        if gpu_native:
            args.append("--gpu-native")
        if no_cache:
            args.append("--no-cache")
        args.append("--refresh-source")

        log_path = Path("/tmp/rebuild-gpu.log")
        cmd_line = f"$ {' '.join(args)}"
        yield cmd_line
        process = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            cwd=str(self._root),
            start_new_session=True,
        )
        self._build_process = process
        try:
            assert process.stdout is not None
            with log_path.open("w") as log_file:
                log_file.write(cmd_line + "\n")
                log_file.flush()
                async for raw_line in process.stdout:
                    line = raw_line.decode(errors="replace").rstrip()
                    log_file.write(line + "\n")
                    log_file.flush()
                    yield line
            exit_code = await process.wait()
            with log_path.open("a") as log_file:
                if exit_code == 0:
                    msg = "Build completed successfully."
                elif exit_code == -signal.SIGTERM or exit_code == -signal.SIGKILL:
                    msg = "Build cancelled."
                else:
                    msg = f"Build FAILED (exit code {exit_code})."
                log_file.write(msg + "\n")
            yield msg
        finally:
            self._build_process = None

    async def start_gateway(self, *, node_id: str = "localhost") -> str:
        """Start Edge+Gateway container via parameterized compose."""
        compose_path = self._root / "docker" / "compose" / "gpu-edge.yml"
        if not compose_path.exists():
            return f"Compose file not found: {compose_path}"

        socket_dir_error = ensure_socket_dir()
        if socket_dir_error:
            return socket_dir_error
        node_env_path = ensure_node_env(self._root, node_id)
        node_env = load_env_file(node_env_path)
        model_path = Path(node_env.get("MODEL_PATH", str(Path.home() / ".models")))
        ownership_error = ensure_bind_mount_dirs(self._root, node_id, model_path)
        if ownership_error:
            return ownership_error
        env = build_service_env(self._root, node_env_path)
        env["COMPOSE_PROJECT_NAME"] = f"edge-{node_id}"

        result = await asyncio.create_subprocess_exec(
            "docker",
            "compose",
            "-f",
            str(compose_path),
            "up",
            "-d",
            "--force-recreate",
            env=env,
            cwd=str(self._root),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        output = await result.communicate()
        text = output[0].decode(errors="replace") if output[0] else ""
        if result.returncode == 0:
            return f"Gateway container started (edge-{node_id}).\n{text}"
        return f"Failed to start Gateway (exit {result.returncode}).\n{text}"

    async def stop_gateway(self) -> str:
        """Stop and remove Gateway container, regardless of how it was started."""
        gateway_info = self._service_state.check_gateway()
        container_name = gateway_info.container_name
        if not container_name:
            return "Gateway is not running."

        stop = await asyncio.create_subprocess_exec(
            "docker",
            "stop",
            container_name,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        stop_out = await stop.communicate()
        if stop.returncode != 0:
            text = stop_out[0].decode(errors="replace").strip() if stop_out[0] else ""
            return f"Failed to stop Gateway ({container_name}, exit {stop.returncode}).\n{text}"

        rm = await asyncio.create_subprocess_exec(
            "docker",
            "rm",
            container_name,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        await rm.communicate()
        return f"Gateway stopped and removed ({container_name})."

    async def start_stargate(self) -> str:
        """Start Stargate as host process, detecting immediate crashes."""
        script = (
            self._root
            / "services"
            / "universal-stargate"
            / "scripts"
            / "start-stargate.sh"
        )
        if not script.exists():
            return f"Script not found: {script}"

        config_path = ensure_stargate_config() # default to ~/.gateway/stargate.yaml
        env = build_service_env(self._root)
        env["STARGATE_CONFIG"] = str(config_path)
        env["STARGATE_MODE"] = "master"

        log_path = Path("/tmp/logs/universal-stargate/tui-startup.log")
        log_path.parent.mkdir(parents=True, exist_ok=True)

        with log_path.open("w") as log_fh:
            process = await asyncio.create_subprocess_exec(
                str(script),
                "debug",
                env=env,
                cwd=str(self._root),
                stdout=log_fh,
                stderr=asyncio.subprocess.STDOUT,
                start_new_session=True,
            )

        try:
            exit_code = await asyncio.wait_for(process.wait(), timeout=3.0)
            tail = log_path.read_text(errors="replace")[-1500:]
            return f"Stargate failed (exit {exit_code}).\n{tail}"
        except TimeoutError:
            self._write_pid_file(process.pid)
            return f"Stargate starting (PID {process.pid})."

    async def start_rag(self) -> str:
        """Start RAG service as host process via uvicorn."""
        venv_python = Path.home() / ".venvs" / "universal" / "bin" / "python"
        python = str(venv_python) if venv_python.exists() else "python3"

        log_path = Path("/tmp/logs/universal-rag")
        log_path.mkdir(parents=True, exist_ok=True)
        log_file = log_path / "rag.log"

        env = build_service_env(self._root)
        libs_path = str(self._root / "libs")
        existing_pythonpath = env.get("PYTHONPATH", "")
        env["PYTHONPATH"] = (
            f"{libs_path}:{existing_pythonpath}" if existing_pythonpath else libs_path
        )

        with (log_file).open("a") as log_fh:
            process = await asyncio.create_subprocess_exec(
                python,
                "-m",
                "uvicorn",
                "services.rag.rag_service:app",
                "--host",
                "0.0.0.0",
                "--port",
                "8100",
                env=env,
                cwd=str(self._root),
                stdout=log_fh,
                stderr=asyncio.subprocess.STDOUT,
                start_new_session=True,
            )

        try:
            exit_code = await asyncio.wait_for(process.wait(), timeout=3.0)
            tail = log_file.read_text(errors="replace")[-1000:]
            return f"RAG service failed (exit {exit_code}).\n{tail}"
        except TimeoutError:
            pid_path = GATEWAY_DIR / "rag.pid"
            GATEWAY_DIR.mkdir(parents=True, exist_ok=True)
            pid_path.write_text(str(process.pid) + "\n")
            return f"RAG service starting (PID {process.pid})."

    async def stop_rag(self) -> str:
        """Stop RAG service gracefully."""
        pid_file = GATEWAY_DIR / "rag.pid"
        port = 8100

        recorded_pid: int | None = None
        if pid_file.exists():
            try:
                recorded_pid = int(pid_file.read_text().strip())
            except (ValueError, OSError) as e:
                logger.error("Corrupt RAG PID file %s: %s", pid_file, e)
                pid_file.unlink(missing_ok=True)

        if recorded_pid is not None and self._service_state._pid_alive(recorded_pid):
            return await self._kill_and_wait(recorded_pid, pid_file, service_name="RAG")

        pid_file.unlink(missing_ok=True)
        if not self._service_state._port_open(port):
            return "RAG service is not running."

        listener = self._service_state._find_listener_pid(port)
        if listener is None:
            return (
                f"Port {port} is open but the listener could not be identified.\n"
                "Run: ss -tlnp 'sport = :8100'"
            )
        return await self._kill_and_wait(listener, None, service_name="RAG")

    async def start_cloud_proxy(self) -> str:
        """Start Cloud Proxy service as host process via uvicorn.

        Ensures ``~/.gateway/cloud-proxy.yaml`` exists (generates a
        template on first run) and reads the configured port and host from it.
        """
        ensure_cloud_proxy_config()
        port = read_cloud_proxy_port()
        host = read_cloud_proxy_host()

        venv_python = Path.home() / ".venvs" / "universal" / "bin" / "python"
        python = str(venv_python) if venv_python.exists() else "python3"

        log_dir = Path("/tmp/logs/universal-cloud-proxy")
        log_dir.mkdir(parents=True, exist_ok=True)
        log_file = log_dir / "cloud-proxy.log"

        env = build_service_env(self._root)
        libs_path = str(self._root / "libs")
        existing_pythonpath = env.get("PYTHONPATH", "")
        env["PYTHONPATH"] = (
            f"{libs_path}:{existing_pythonpath}" if existing_pythonpath else libs_path
        )

        with log_file.open("a") as log_fh:
            process = await asyncio.create_subprocess_exec(
                python,
                "-m",
                "uvicorn",
                "services.universal_cloud_proxy.cloud_proxy:app",
                "--host",
                host,
                "--port",
                str(port),
                env=env,
                cwd=str(self._root),
                stdout=log_fh,
                stderr=asyncio.subprocess.STDOUT,
                start_new_session=True,
            )

        try:
            exit_code = await asyncio.wait_for(process.wait(), timeout=3.0)
            tail = log_file.read_text(errors="replace")[-1000:]
            return f"Cloud Proxy failed (exit {exit_code}).\n{tail}"
        except TimeoutError:
            pid_path = GATEWAY_DIR / "cloud-proxy.pid"
            GATEWAY_DIR.mkdir(parents=True, exist_ok=True)
            pid_path.write_text(str(process.pid) + "\n")
            return f"Cloud Proxy starting on {host}:{port} (PID {process.pid})."

    async def stop_cloud_proxy(self) -> str:
        """Stop Cloud Proxy service gracefully."""
        pid_file = GATEWAY_DIR / "cloud-proxy.pid"
        port = read_cloud_proxy_port()

        recorded_pid: int | None = None
        if pid_file.exists():
            try:
                recorded_pid = int(pid_file.read_text().strip())
            except (ValueError, OSError) as e:
                logger.error("Corrupt Cloud Proxy PID file %s: %s", pid_file, e)
                pid_file.unlink(missing_ok=True)

        if recorded_pid is not None and self._service_state._pid_alive(recorded_pid):
            return await self._kill_and_wait(
                recorded_pid, pid_file, service_name="Cloud Proxy"
            )

        pid_file.unlink(missing_ok=True)
        if not self._service_state._port_open(port):
            return "Cloud Proxy is not running."

        listener = self._service_state._find_listener_pid(port)
        if listener is None:
            return (
                f"Port {port} is open but the listener could not be identified.\n"
                "Run: ss -tlnp 'sport = :8200'"
            )
        return await self._kill_and_wait(listener, None, service_name="Cloud Proxy")

    @property
    def sidecar(self) -> SidecarController:
        return self._sidecar

    async def stop_stargate(self) -> str:
        """Stop Stargate regardless of whether the PID file is current.

        Three cases handled in order:
        1. PID file present and alive → SIGTERM the recorded PID.
        2. PID file absent/stale but port open → locate listener via ss(8).
        3. Port closed → nothing to do.
        """
        pid_file = GATEWAY_DIR / "stargate.pid"
        port = ServiceState.STARGATE_PORT
        port_open = self._service_state._port_open(port)

        recorded_pid: int | None = None
        if pid_file.exists():
            try:
                recorded_pid = int(pid_file.read_text().strip())
            except (ValueError, OSError) as e:
                logger.error("Corrupt PID file %s: %s", pid_file, e)
                pid_file.unlink(missing_ok=True)

        if recorded_pid is not None and self._service_state._pid_alive(recorded_pid):
            if not port_open:
                pid_file.unlink(missing_ok=True)
                return (
                    f"PID {recorded_pid} is alive but port {port} is closed — "
                    "not Stargate. Stale PID file removed."
                )
            return await self._kill_and_wait(recorded_pid, pid_file)

        pid_file.unlink(missing_ok=True)
        if not port_open:
            if recorded_pid is not None:
                return f"Stale PID file removed (PID {recorded_pid} already dead)."
            return "Stargate is not running."

        listener = self._service_state._find_listener_pid(port)
        if listener is None:
            return (
                f"Port {port} is open but the listener could not be identified.\n"
                "Run: ss -tlnp 'sport = :9999'"
            )
        return await self._kill_and_wait(listener, None)

    async def _kill_and_wait(
        self,
        pid: int,
        pid_file: Path | None,
        *,
        service_name: str = "Stargate",
        sigterm_timeout: float = 8.0,
        sigkill_timeout: float = 4.0,
    ) -> str:
        """Send SIGTERM, poll for death, escalate to SIGKILL if needed."""
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            if pid_file is not None:
                pid_file.unlink(missing_ok=True)
            return f"{service_name} already exited."
        except PermissionError as e:
            logger.error("Cannot kill %s PID %d: %s", service_name, pid, e)
            return f"Cannot stop {service_name} (PID {pid}): {e}"

        if pid_file is not None:
            pid_file.unlink(missing_ok=True)

        alive = self._service_state._pid_alive
        t0 = asyncio.get_running_loop().time()

        while asyncio.get_running_loop().time() - t0 < sigterm_timeout:
            await asyncio.sleep(0.3)
            if not alive(pid):
                elapsed = asyncio.get_running_loop().time() - t0
                return f"{service_name} stopped (PID {pid}, {elapsed:.1f}s)."

        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            elapsed = asyncio.get_running_loop().time() - t0
            return f"{service_name} stopped (PID {pid}, {elapsed:.1f}s)."

        t1 = asyncio.get_running_loop().time()
        while asyncio.get_running_loop().time() - t1 < sigkill_timeout:
            await asyncio.sleep(0.3)
            if not alive(pid):
                elapsed = asyncio.get_running_loop().time() - t0
                return f"{service_name} SIGKILL'd after {elapsed:.1f}s (PID {pid})."

        elapsed = asyncio.get_running_loop().time() - t0
        return (
            f"{service_name} may still be running "
            f"(could not confirm death, PID {pid}, {elapsed:.1f}s)."
        )

    @staticmethod
    def _write_pid_file(pid: int) -> None:
        GATEWAY_DIR.mkdir(parents=True, exist_ok=True)
        pid_path = GATEWAY_DIR / "stargate.pid"
        pid_path.write_text(str(pid) + "\n")
