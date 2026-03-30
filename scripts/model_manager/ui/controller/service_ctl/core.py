"""Core ServiceController — Docker builds, Gateway, Stargate, sidecar lifecycle.

Part of the model_manager UI controller. Orchestrates builds and service
start/stop; delegates RAG and Cloud Proxy to sibling modules.
"""

from __future__ import annotations

import asyncio
import logging
import os
import signal
import time
from pathlib import Path
from typing import TYPE_CHECKING

from scripts.model_manager.observation_event import (
    emit_build_image_completed,
    emit_build_image_started,
)

from ...model.build_state import BuildState, BuildStatus, ImageInfo
from ...model.service_state import ServiceState
from ..service_config import (
    GATEWAY_DIR,
    NODES_DIR,
    build_mcp_env,
    build_service_env,
    ensure_bind_mount_dirs,
    ensure_node_env,
    ensure_socket_dir,
    ensure_stargate_config,
    load_env_file,
    load_mcp_config,
    mcp_browser_override_path,
)
from ..sidecar_ctl import SidecarController
from . import (
    agent_bus_service,
    cloud_proxy_service,
    cortex_api_service,
    event_service,
    rag_service,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

logger = logging.getLogger(__name__)

_PGID_KILL_TIMEOUT = 5
_CURSOR_DESCRIPTOR_REFRESH_TIMEOUT_S = 30
_GATEWAY_CRASH_DETECT_S = 5.0
_MCP_HEALTH_WAIT_TIMEOUT_S = 60.0
_MCP_HEALTH_POLL_INTERVAL_S = 1.0
_BUILD_LOG_POLL_INTERVAL_S = 0.25


async def _pump_build_log(
    build_log: Path,
    process: asyncio.subprocess.Process,
    queue: asyncio.Queue[str | object],
    sentinel: object,
) -> None:
    """Stream appended build-log lines until the subprocess and log are drained."""
    offset = 0
    while True:
        emitted = False
        if build_log.exists():
            with build_log.open(encoding="utf-8", errors="replace") as fh:
                fh.seek(offset)
                while line := fh.readline():
                    emitted = True
                    offset = fh.tell()
                    await queue.put(line.rstrip())
        if process.returncode is not None:
            if emitted:
                continue
            break
        await asyncio.sleep(_BUILD_LOG_POLL_INTERVAL_S)
    await queue.put(sentinel)


class ServiceController:
    """
    Orchestrates Docker builds and the lifecycle of Gateway, Stargate, MCP,
    RAG, Cloud Proxy, and Event Service.

    Delegates to existing shell scripts for core logic; does not reimplement it.
    """

    def __init__(self, workspace_root: Path) -> None:
        self._root = workspace_root
        self._build_state = BuildState(workspace_root)
        self._service_state = ServiceState(workspace_root)
        self._sidecar = SidecarController(workspace_root)
        self._build_process: asyncio.subprocess.Process | None = None

    @property
    def service_state(self) -> ServiceState:
        return self._service_state

    def check_model_path_ownership(self) -> str | None:
        """Return warning if MODEL_PATH is root-owned, None if OK.

        Returns:
            A warning string if MODEL_PATH is root-owned and the current user is not root, otherwise None.
        """
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
        info = self._build_state.check_image()
        if self.build_running:
            info.status = BuildStatus.BUILDING
        return info

    def check_build_cache(self, target: str = "gateway") -> str:
        return self._build_state.check_build_cache(target)

    async def prune_build_cache(self, target: str = "gateway") -> str:
        return await self._build_state.prune_build_cache(target)

    async def cancel_build(self) -> str:
        """Kill the running build process group."""
        proc = self._build_process
        if proc is None or proc.returncode is not None:
            return "No build in progress."
        try:
            # The build process is started with start_new_session=True,
            # so its PGID is its PID.
            pgid = proc.pid
            os.killpg(pgid, signal.SIGTERM)
            try:
                await asyncio.wait_for(proc.wait(), timeout=_PGID_KILL_TIMEOUT)
            except TimeoutError:
                os.killpg(pgid, signal.SIGKILL)
                await proc.wait()
        except (ProcessLookupError, PermissionError) as e:
            logger.warning("cancel_build: %s", e)
        return "Build cancelled."

    async def build_image(
        self,
        *,
        scope: str = "all",
        no_cache: bool = True,
        gpu_native: bool = True,
        cpu_native: bool = True,
    ) -> AsyncIterator[str]:
        """
        Build Docker GPU image via build-gpu.sh, yielding log lines.

        By default *no_cache* is true: ``build-gpu.sh`` runs with ``--no-cache --pull``
        so base images and all layers are rebuilt. Pass ``no_cache=False`` only for
        a faster incremental build (not used by the Services UI).

        Yields lines from stdout/stderr as they appear.
        """
        if self.build_running:
            yield "ERROR: Build already in progress. Cancel or wait for it to finish."
            return

        script = self._root / "docker" / "scripts" / "build" / "build-gpu.sh"
        if not script.exists():
            yield f"ERROR: Build script not found: {script}"
            return

        args = [
            str(script),
            *(["--cpu-native"] if cpu_native else []),
            *(["--gpu-native"] if gpu_native else []),
            *(["--no-cache"] if no_cache else []),
            *(["--no-vllm"] if scope == "llama" else []),
        ]
        # Full rebuild (--no-cache) already re-runs every COPY; --refresh-source only
        # matters for cache-preserving builds.
        if not no_cache:
            args.append("--refresh-source")

        env = build_service_env(self._root)
        build_log = Path(
            f"/tmp/gateway-build-{os.getpid()}-{int(time.time() * 1000)}.log"
        )
        env["BUILD_LOG_PATH"] = str(build_log)
        cmd_line = f"$ {' '.join(args)}"
        yield cmd_line
        build_t0 = time.monotonic()
        await emit_build_image_started(host="localhost", scope=scope)
        process = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            cwd=str(self._root),
            env=env,
            start_new_session=True,
        )
        self._build_process = process
        try:
            if process.stdout is None:
                yield "ERROR: Could not capture build output."
                return
            queue: asyncio.Queue[str | object] = asyncio.Queue()
            stdout_done = object()
            build_log_done = object()

            async def _pump_stdout() -> None:
                assert process.stdout is not None
                async for raw_line in process.stdout:
                    await queue.put(raw_line.decode(errors="replace").rstrip())
                await queue.put(stdout_done)

            async with asyncio.TaskGroup() as tg:
                tg.create_task(_pump_stdout())
                tg.create_task(
                    _pump_build_log(
                        build_log=build_log,
                        process=process,
                        queue=queue,
                        sentinel=build_log_done,
                    )
                )
                completed = 0
                while completed < 2:
                    item = await queue.get()
                    if item is stdout_done or item is build_log_done:
                        completed += 1
                        continue
                    yield str(item)
            exit_code = await process.wait()
            if exit_code == 0:
                msg = "Build completed successfully."
            elif exit_code == -signal.SIGTERM or exit_code == -signal.SIGKILL:
                msg = "Build cancelled."
            else:
                msg = f"Build FAILED (exit code {exit_code})."
            yield msg
        finally:
            if process.returncode is None:
                try:
                    os.killpg(process.pid, signal.SIGTERM)
                except (ProcessLookupError, PermissionError):
                    pass
            self._build_process = None
            build_ok = (
                process.returncode == 0 if process.returncode is not None else False
            )
            await emit_build_image_completed(
                host="localhost",
                scope=scope,
                success=build_ok,
                duration_s=time.monotonic() - build_t0,
            )

    async def _run_build_script(
        self,
        *,
        script: Path,
        env: dict[str, str],
        no_cache: bool = False,
        extra_args: list[str] | None = None,
    ) -> tuple[int, str]:
        """Run one service image build script and return (exit_code, merged output)."""
        args = [str(script)]
        if extra_args:
            args.extend(extra_args)
        elif no_cache:
            args.append("--no-cache")
        proc = await asyncio.create_subprocess_exec(
            "bash",
            *args,
            env=env,
            cwd=str(self._root),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        out = await proc.communicate()
        text = out[0].decode(errors="replace") if out[0] else ""
        return proc.returncode, text

    async def start_gateway(self, *, node_id: str = "localhost") -> str:
        """Start Edge+Gateway container via parameterized compose.

        Python source under ``libs/`` and ``services/`` is bind-mounted from the
        repo (see ``docker/compose/gpu-edge.yml``). Reuse the existing image until
        you run an explicit rebuild (Services → Build Image / ``--build`` on relay).
        """
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
            container_name = f"edge-{node_id}"
            crash = await self._detect_container_crash(
                container_name, wait_s=_GATEWAY_CRASH_DETECT_S
            )
            if crash is not None:
                return (
                    f"Gateway exited immediately ({container_name}).\n{text}\n{crash}"
                )
            return f"Gateway container started (edge-{node_id}).\n{text}"
        logger.error("Failed to start Gateway (exit %d):\n%s", result.returncode, text)
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
            logger.error(
                "Failed to stop Gateway (%s, exit %d):\n%s",
                container_name,
                stop.returncode,
                text,
            )
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

        config_path = ensure_stargate_config()  # default to ~/.gateway/stargate.yaml
        env = build_service_env(self._root)
        env["STARGATE_CONFIG"] = str(config_path)
        env["STARGATE_MODE"] = "master"

        log_path = Path("/tmp/logs/universal-stargate/tui-startup.log")
        try:
            log_path.parent.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            logger.error(
                "Failed to create log directory for Stargate: %s",
                e,
            )
            return f"Failed to start Stargate: could not set up logging ({e})."

        try:
            log_fh = log_path.open("w")
        except OSError as e:
            logger.error("Failed to open Stargate startup log %s: %s", log_path, e)
            return f"Failed to start Stargate: {e}"

        try:
            process = await asyncio.create_subprocess_exec(
                str(script),
                "debug",
                env=env,
                cwd=str(self._root),
                stdout=log_fh,
                stderr=asyncio.subprocess.STDOUT,
                start_new_session=True,
            )
            # Child process owns its duplicated FD; parent can close immediately.
            log_fh.close()
        except OSError as e:
            logger.error("Failed to start Stargate subprocess: %s", e)
            log_fh.close()
            return f"Failed to start Stargate: {e}"

        if process.returncode is not None:
            tail = log_path.read_text(errors="replace")[-1500:]
            return f"Stargate failed (exit {process.returncode}).\n{tail}"

        self._write_pid_file(process.pid)
        try:
            exit_code = await asyncio.wait_for(process.wait(), timeout=3.0)
            tail = log_path.read_text(errors="replace")[-1500:]
            pid_path = GATEWAY_DIR / "stargate.pid"
            pid_path.unlink(missing_ok=True)
            return f"Stargate failed (exit {exit_code}).\n{tail}"
        except TimeoutError:
            return f"Stargate starting (PID {process.pid})."

    async def start_rag(self) -> str:
        """Start RAG service as host process via uvicorn."""
        return await rag_service.start_rag(
            self._service_state, self._root, self._kill_and_wait
        )

    async def stop_rag(self) -> str:
        """Stop RAG service gracefully."""
        return await rag_service.stop_rag(
            self._service_state, self._root, self._kill_and_wait
        )

    async def start_cloud_proxy(self) -> str:
        """Start Cloud Proxy service as host process via uvicorn."""
        return await cloud_proxy_service.start_cloud_proxy(
            self._service_state, self._root, self._kill_and_wait
        )

    async def stop_cloud_proxy(self) -> str:
        """Stop Cloud Proxy service gracefully."""
        return await cloud_proxy_service.stop_cloud_proxy(
            self._service_state, self._root, self._kill_and_wait
        )

    def _mcp_compose_args(self) -> tuple[list[str], Path] | None:
        """Return (docker compose args, compose_path) or None if missing.

        When browser tools are enabled in ~/.gateway/mcp.yaml, appends the
        browser override file which applies the narrow seccomp relaxation.

        Args:
            self: The instance of ServiceController (implicitly uses self._root).

        Returns:
            (args, compose_path) for docker compose, or None if compose file absent.
        """
        compose_path = self._root / "docker" / "compose" / "mcp-server.yml"
        if not compose_path.exists():
            return None
        args = ["docker", "compose", "-f", str(compose_path)]
        override = mcp_browser_override_path(self._root)
        if override is not None and override.exists():
            args.extend(["-f", str(override)])
        return args, compose_path

    async def start_mcp(self) -> str:
        """Start MCP server container via docker compose."""
        base = self._mcp_compose_args()
        if base is None:
            return "Compose file not found: docker/compose/mcp-server.yml"
        args, _ = base
        env = build_mcp_env(self._root)

        # Example of refactored call (actual implementation would need a helper)
        # return await self._run_docker_compose_command(
        #     compose_path=compose_path,
        #     command="up",
        #     args=["-d", "--force-recreate"],
        #     env=env,
        #     success_msg="MCP server started.",
        #     failure_msg="Failed to start MCP server"
        # )

        result = await asyncio.create_subprocess_exec(
            *args,
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
            return f"MCP server started.\n{text}"
        logger.error(
            "Failed to start MCP server (exit %d):\n%s", result.returncode, text
        )
        return f"Failed to start MCP server (exit {result.returncode}).\n{text}"

    async def stop_mcp(self) -> str:
        """Stop and remove MCP server container."""
        base = self._mcp_compose_args()
        if base is None:
            return "MCP server is not running (compose file missing)."
        args, _ = base
        env = build_mcp_env(self._root)

        result = await asyncio.create_subprocess_exec(
            *args,
            "down",
            env=env,
            cwd=str(self._root),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        output = await result.communicate()
        text = output[0].decode(errors="replace") if output[0] else ""
        if result.returncode == 0:
            return f"MCP server stopped.\n{text}"
        logger.error(
            "Failed to stop MCP server (exit %d):\n%s", result.returncode, text
        )
        return f"Failed to stop MCP server (exit {result.returncode}).\n{text}"

    async def rebuild_mcp(self, *, no_cache: bool = False) -> str:
        """Rebuild MCP server image from source and restart.

        Default is a cached rebuild that refreshes only source layers — fast
        enough for the sync+restart workflow.  Pass ``no_cache=True`` for a
        full fresh build (pulls base images, rebuilds all layers).
        """
        base = self._mcp_compose_args()
        if base is None:
            return "Compose file not found: docker/compose/mcp-server.yml"
        env = build_mcp_env(self._root)
        script = self._root / "docker" / "scripts" / "build" / "build-mcp.sh"
        extra_args = ["--no-cache"] if no_cache else ["--refresh-source"]
        build_code, build_text = await self._run_build_script(
            script=script,
            env=env,
            extra_args=extra_args,
        )
        build_failed = build_code != 0
        if build_failed:
            logger.error("MCP rebuild failed (exit %d):\n%s", build_code, build_text)
        start_text = await self.start_mcp()
        if build_failed:
            return (
                f"MCP build failed (exit {build_code}) — "
                f"restarted with existing image.\n{start_text}"
            )
        if not start_text.startswith("MCP server started."):
            return start_text

        health_error = await self._wait_mcp_healthy(timeout=_MCP_HEALTH_WAIT_TIMEOUT_S)
        if health_error is not None:
            return f"{start_text}\nWARNING: {health_error}"

        refresh_text = await self._refresh_cursor_mcp_descriptors_if_enabled()
        if refresh_text:
            return f"{start_text}\n{refresh_text}"
        return start_text

    async def start_cortex_api(self) -> str:
        """Start cortex-api as host subprocess."""
        return await cortex_api_service.start_cortex_api(
            self._service_state, self._root, self._kill_and_wait
        )

    async def stop_cortex_api(self) -> str:
        """Stop cortex-api gracefully."""
        return await cortex_api_service.stop_cortex_api(
            self._service_state, self._root, self._kill_and_wait
        )

    async def restart_cortex_api(self) -> str:
        """Restart cortex-api (stop then start)."""
        await self.stop_cortex_api()
        return await self.start_cortex_api()

    async def rebuild_cortex_api(self, *, no_cache: bool = False) -> str:  # noqa: ARG002
        """Rebuild cortex-api — host process, so rebuild = restart."""
        await self.stop_cortex_api()
        return await self.start_cortex_api()

    async def start_agent_bus(self) -> str:
        """Start agent-bus as host subprocess."""
        return await agent_bus_service.start_agent_bus(
            self._service_state, self._root, self._kill_and_wait
        )

    async def stop_agent_bus(self) -> str:
        """Stop agent-bus gracefully."""
        return await agent_bus_service.stop_agent_bus(
            self._service_state, self._root, self._kill_and_wait
        )

    async def restart_agent_bus(self) -> str:
        """Restart agent-bus (stop then start)."""
        await self.stop_agent_bus()
        return await self.start_agent_bus()

    async def rebuild_agent_bus(self, *, no_cache: bool = False) -> str:  # noqa: ARG002
        """Rebuild agent-bus — host process, so rebuild = restart."""
        await self.stop_agent_bus()
        return await self.start_agent_bus()

    async def _wait_mcp_healthy(self, *, timeout: float) -> str | None:
        """Wait until the mcp-server container reports healthy."""
        return await self._wait_container_healthy("mcp-server", timeout=timeout)

    async def _refresh_cursor_mcp_descriptors_if_enabled(self) -> str:
        """Optionally refresh Cursor MCP descriptors based on ~/.gateway/mcp.yaml."""
        cfg = load_mcp_config()
        if cfg is None or not cfg.refresh_cursor_descriptors_after_rebuild:
            return ""

        script = self._root / "scripts" / "refresh-cursor-mcp-descriptors"
        if not script.exists():
            msg = (
                "WARNING: Cursor descriptor refresh is enabled, but script is missing: "
                f"{script}"
            )
            logger.warning(msg)
            return msg

        try:
            proc = await asyncio.create_subprocess_exec(
                "bash",
                str(script),
                cwd=str(self._root),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
        except OSError as exc:
            msg = f"WARNING: Could not launch descriptor refresh script: {exc}"
            logger.warning(msg)
            return msg

        try:
            output = await asyncio.wait_for(
                proc.communicate(),
                timeout=_CURSOR_DESCRIPTOR_REFRESH_TIMEOUT_S,
            )
        except TimeoutError:
            proc.kill()
            await proc.communicate()
            msg = (
                "WARNING: Cursor descriptor refresh timed out after "
                f"{_CURSOR_DESCRIPTOR_REFRESH_TIMEOUT_S}s"
            )
            logger.warning(msg)
            return msg

        text = output[0].decode(errors="replace").strip() if output[0] else ""
        if proc.returncode == 0:
            return f"Cursor MCP descriptors refreshed.{chr(10) + text if text else ''}"

        msg = f"WARNING: Cursor descriptor refresh failed (exit {proc.returncode})."
        logger.warning("%s\n%s", msg, text)
        if text:
            return f"{msg}\n{text}"
        return msg

    async def start_event_service(self) -> str:
        """Start event service as host subprocess."""
        return await event_service.start_event_service(
            self._service_state, self._root, self._kill_and_wait
        )

    async def stop_event_service(self) -> str:
        """Stop event service gracefully."""
        return await event_service.stop_event_service(
            self._service_state, self._root, self._kill_and_wait
        )

    async def restart_event_service(self) -> str:
        """Restart event service (stop then start)."""
        await self.stop_event_service()
        return await self.start_event_service()

    async def rebuild_event_service(self, *, no_cache: bool = False) -> str:  # noqa: ARG002
        """Rebuild event service — host process, so rebuild = restart."""
        await self.stop_event_service()
        return await self.start_event_service()

    async def _detect_container_crash(
        self, container_name: str, *, wait_s: float
    ) -> str | None:
        """Wait *wait_s* seconds then check if the container exited.

        Returns None if the container is still running (success), or a
        diagnostic message if it already exited/died.  Much faster than
        _wait_container_healthy because it doesn't wait for Docker's
        start_period — just catches immediate crashes.
        """
        await asyncio.sleep(wait_s)
        inspect = await asyncio.create_subprocess_exec(
            "docker",
            "inspect",
            "--format",
            "{{.State.Status}}",
            container_name,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        output = await inspect.communicate()
        status = output[0].decode(errors="replace").strip().lower()
        if inspect.returncode != 0:
            return f"{container_name} not found after start."
        if status in {"exited", "dead"}:
            return f"{container_name} exited within {wait_s:.0f}s (state: {status})."
        return None

    async def _wait_container_healthy(
        self, container_name: str, *, timeout: float
    ) -> str | None:
        """Wait until *container_name* reports healthy.

        Returns None when healthy, otherwise a diagnostic message.
        """
        deadline = asyncio.get_running_loop().time() + timeout
        last_status = "unknown"
        while asyncio.get_running_loop().time() < deadline:
            inspect = await asyncio.create_subprocess_exec(
                "docker",
                "inspect",
                "--format",
                "{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}",
                container_name,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
            output = await inspect.communicate()
            status = output[0].decode(errors="replace").strip().lower()
            if inspect.returncode != 0:
                last_status = "missing"
            elif status:
                last_status = status
                if status == "healthy":
                    return None
                if status in {"exited", "dead"}:
                    return (
                        f"{container_name} did not become healthy "
                        f"(container state: {status})."
                    )
            await asyncio.sleep(_MCP_HEALTH_POLL_INTERVAL_S)
        return (
            f"{container_name} health check timed out after "
            f"{timeout:.0f}s (last status: {last_status})."
        )

    async def check_mcp(self) -> str:
        """Return docker ps output for the mcp-server container."""
        result = await asyncio.create_subprocess_exec(
            "docker",
            "ps",
            "--filter",
            "name=mcp-server",
            "--format",
            "table {{.Names}}\t{{.Status}}\t{{.Ports}}",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        output = await result.communicate()
        return output[0].decode(errors="replace") or "No output."

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

        recorded_pid: int | None = None
        if pid_file.exists():
            try:
                recorded_pid = int(pid_file.read_text().strip())
            except (ValueError, OSError) as e:
                logger.error("Corrupt PID file %s: %s", pid_file, e, exc_info=True)
                pid_file.unlink(missing_ok=True)

        if recorded_pid is not None and self._service_state._pid_alive(recorded_pid):
            # PID is alive, now check if it's actually listening on the port
            port_open = self._service_state._port_open(port)
            if not port_open:
                pid_file.unlink(missing_ok=True)
                logger.warning(
                    "PID %d is alive but port %d is closed; removing stale PID and checking listener",
                    recorded_pid,
                    port,
                )
                recorded_pid = None
            else:
                return await self._kill_and_wait(recorded_pid, pid_file)

        pid_file.unlink(missing_ok=True)
        port_open = self._service_state._port_open(port)
        if not port_open:
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
            if pid_file is not None:
                pid_file.unlink(
                    missing_ok=True
                )  # Unlink even on PermissionError, as we can't manage it.
            return f"Cannot stop {service_name} (PID {pid}): {e}"

        if pid_file is not None:
            pid_file.unlink(missing_ok=True)

        alive = self._service_state._pid_alive
        t_start = asyncio.get_running_loop().time()

        while asyncio.get_running_loop().time() - t_start < sigterm_timeout:
            await asyncio.sleep(0.3)
            if not alive(pid):
                total_elapsed = asyncio.get_running_loop().time() - t_start
                return f"{service_name} stopped (PID {pid}, {total_elapsed:.1f}s)."

        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            total_elapsed = asyncio.get_running_loop().time() - t_start
            return f"{service_name} stopped (PID {pid}, {total_elapsed:.1f}s)."

        t1 = asyncio.get_running_loop().time()
        while asyncio.get_running_loop().time() - t1 < sigkill_timeout:
            await asyncio.sleep(0.3)
            if not alive(pid):
                total_elapsed = asyncio.get_running_loop().time() - t_start
                return (
                    f"{service_name} SIGKILL'd after {total_elapsed:.1f}s (PID {pid})."
                )

        total_elapsed = asyncio.get_running_loop().time() - t_start
        return (
            f"{service_name} may still be running "
            f"(could not confirm death, PID {pid}, {total_elapsed:.1f}s)."
        )

    def _write_pid_file(self, pid: int) -> None:
        """Writes the given PID to the Stargate PID file.

        Args:
            pid: The process ID to write.
        """
        GATEWAY_DIR.mkdir(parents=True, exist_ok=True)
        pid_path = GATEWAY_DIR / "stargate.pid"
        pid_path.write_text(str(pid) + "\n")
