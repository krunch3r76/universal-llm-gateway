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
from typing import TYPE_CHECKING, Any

from scripts.model_manager.observation_event import (
    emit_build_image_completed,
    emit_build_image_started,
)

from ...model.build_state import BuildState, BuildStatus, ImageInfo
from ...model.service_state import ServiceState
from ..git_worker_drain_supervisor import build_git_worker_drain_supervisor
from ..gpu_docker_preflight import check_gpu_docker_prerequisites
from ..restart_drain import (
    GIT_INTEGRATION_WORKER_URL,
    RestartDrainGate,
)
from ..restart_intent_store import RestartIntentStore
from ..service_config import (
    GATEWAY_DIR,
    NODES_DIR,
    apply_checkout_code_version,
    build_service_env,
    cdp_ask_manage_state,
    cdp_ask_url_config,
    ensure_bind_mount_dirs,
    ensure_node_env,
    ensure_socket_dir,
    ensure_stargate_config,
    is_cdp_ask_local_host,
    load_env_file,
)
from ..shutdown_gate import ManageShutdownGate
from ..sidecar_ctl import SidecarController
from . import (
    agent_bus_service,
    cdp_ask_remote,
    cdp_ask_service,
    cloud_proxy_service,
    cortex_api_service,
    event_service,
    git_integration_worker_service,
    mcp_service,
    rag_service,
)
from .host_spawn import await_popen_started, spawn_detached_host_process
from .startup_probe import StartupOutcome

try:
    from .local import email_bridge_service as _email_bridge_svc
except ImportError:
    _email_bridge_svc = None

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Awaitable, Callable

    from ..git_worker_drain_supervisor import GitWorkerDrainSupervisor

logger = logging.getLogger(__name__)

# ∀ host subprocess: detach stdin so children do not share the manage TUI tty.
_DETACHED_STDIN = asyncio.subprocess.DEVNULL

_PGID_KILL_TIMEOUT = 5
_GATEWAY_CRASH_DETECT_S = 5.0
_GATEWAY_STOP_TIMEOUT_S = 3
_MCP_HEALTH_POLL_INTERVAL_S = 1.0
_BUILD_LOG_POLL_INTERVAL_S = 0.25
_DEFAULT_STARGATE_SHUTDOWN_GRACE_S = 20.0
_STARGATE_SHUTDOWN_BUFFER_S = 2.0
# git-integration-worker SIGTERM budget — defense-in-depth for an unmanaged/direct
# kill (the event-driven drain is the primary convergence mechanism). Default grace
# aligns with the worker lifespan drain budget (GIT_WORKER_DRAIN_LIFESPAN_TIMEOUT,
# default 30s) so a managed SIGTERM does not truncate the worker's own wait_idle.
_DEFAULT_GIT_WORKER_SHUTDOWN_GRACE_S = 30.0
_GIT_WORKER_SHUTDOWN_BUFFER_S = 2.0
# Intent deadline for the drain supervisor — a true last resort (~10 min) that
# should essentially never fire; observability (manage.restart.draining) is the
# mechanism, not the timer. Env-overridable for tests / tuning.
_GIT_WORKER_DRAIN_DEADLINE_S = float(
    os.environ.get("GIT_WORKER_DRAIN_DEADLINE_S", "600")
)


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
        # Drain-aware restart coordination — persists across manage calls so the
        # per-service restart mutex coalesces concurrent agents / TUI.
        self._restart_gate = RestartDrainGate()
        # Durable restart-intent store (event-driven deferred restart, P2). The
        # singleton survives across manage calls; pending intents are reconciled
        # at boot via reconcile_pending_restart_intents().
        self._restart_intent_store = RestartIntentStore.instance()
        # Process-level quit guard — tracks in-flight manage.sock JSON-RPC and
        # long-running TUI activities so ./manage cannot exit mid-operation.
        self._shutdown_gate = ManageShutdownGate()
        # Optional hook: ManageApp registers in-process charter tick reload.
        self._charter_tick_reload = None

    def set_charter_tick_reload(self, hook) -> None:
        """Register async ``() -> dict`` that reloads charter-runner in-process."""
        self._charter_tick_reload = hook

    async def reload_charter_tick(self) -> dict:
        """Invoke the registered charter-tick reload hook (manage.sock)."""
        hook = self._charter_tick_reload
        if hook is None:
            raise ValueError(
                "charter_reload not registered — ./manage TUI must be running "
                "with a boot that wires set_charter_tick_reload"
            )
        return await hook()

    async def charter_pause(
        self,
        *,
        reason: str = "",
        set_by: str = "manage",
        timeout_s: float = 1800.0,
        poll_s: float = 2.0,
    ) -> dict:
        """Arm durable tick hold and block until in-flight charter dispatches finish.

        Hold is written immediately (no new admits). Then waits until the current
        ``_tick_once`` returns **and** GIW reports no live charter-shaped
        cursor-sdk dispatches. Hold stays armed on timeout.
        """
        import asyncio

        from scripts.model_manager import observation_event as events
        from scripts.model_manager.ui.controller.charter_runner.kernel import hold

        payload = hold.set_hold(reason, set_by)
        await events.emit_manage_charter_tick_paused(
            reason=payload.reason,
            set_by=payload.set_by,
            set_at=payload.set_at,
        )
        started = time.monotonic()
        deadline = started + max(0.0, float(timeout_s))
        live_probe = hold.LiveCharterDispatchProbe(
            probe_status="ok",
            dispatches=[],
        )
        tick_in_flight = True
        while True:
            tick_in_flight = "charter_tick" in self._shutdown_gate.snapshot().activities
            live_probe = await hold.list_live_charter_dispatches()
            if hold.pause_drain_clear(
                held=payload,
                tick_in_flight=tick_in_flight,
                live_probe=live_probe,
            ):
                waited_s = time.monotonic() - started
                return {
                    "status": "ok",
                    "held": True,
                    "drained": True,
                    "reason": payload.reason,
                    "set_by": payload.set_by,
                    "set_at": payload.set_at,
                    "tick_in_flight": False,
                    "live_charter_shaped_dispatches": live_probe.dispatches,
                    "giw_charter_probe_status": live_probe.probe_status,
                    "evaluated_scope": live_probe.evaluated_scope,
                    "waited_s": round(waited_s, 3),
                    "pause_drain_clear": True,
                    "path": str(hold.hold_path()),
                }
            if time.monotonic() >= deadline:
                waited_s = time.monotonic() - started
                return {
                    "status": "timeout",
                    "held": True,
                    "drained": False,
                    "reason": payload.reason,
                    "set_by": payload.set_by,
                    "set_at": payload.set_at,
                    "tick_in_flight": tick_in_flight,
                    "live_charter_shaped_dispatches": live_probe.dispatches,
                    "giw_charter_probe_status": live_probe.probe_status,
                    "evaluated_scope": live_probe.evaluated_scope,
                    "waited_s": round(waited_s, 3),
                    "pause_drain_clear": False,
                    "path": str(hold.hold_path()),
                }
            await asyncio.sleep(max(0.1, float(poll_s)))

    async def charter_resume(self) -> dict:
        """Clear durable tick hold; next interval runs a normal tick."""
        from scripts.model_manager import observation_event as events
        from scripts.model_manager.ui.controller.charter_runner.kernel import hold

        prior = hold.read_hold()
        was_held = hold.clear_hold()
        await events.emit_manage_charter_tick_resumed(
            was_held=was_held,
            reason=prior.reason if prior is not None else None,
        )
        return {
            "status": "ok",
            "held": False,
            "was_held": was_held,
            "path": str(hold.hold_path()),
        }

    async def charter_hold_status(self) -> dict:
        """Report durable hold + pause-drain facts (charter-shaped GIW probe only)."""
        from scripts.model_manager.ui.controller.charter_runner.kernel import hold

        held = hold.read_hold()
        tick_in_flight = "charter_tick" in self._shutdown_gate.snapshot().activities
        live_probe = await hold.list_live_charter_dispatches()
        pause_drain_clear = hold.pause_drain_clear(
            held=held,
            tick_in_flight=tick_in_flight,
            live_probe=live_probe,
        )
        result: dict = {
            "held": held is not None,
            "tick_in_flight": tick_in_flight,
            "live_charter_shaped_dispatches": live_probe.dispatches,
            "giw_charter_probe_status": live_probe.probe_status,
            "evaluated_scope": live_probe.evaluated_scope,
            "pause_drain_clear": pause_drain_clear,
            "path": str(hold.hold_path()),
        }
        if live_probe.error_class is not None:
            result["giw_charter_probe_error_class"] = live_probe.error_class
        if held is not None:
            result.update(hold.hold_as_dict(held) or {})
        return result

    async def charter_block_root(
        self,
        *,
        root_id: str,
        reason: str = "",
        set_by: str = "manage",
        unenroll: bool = True,
        clear_wip: bool = False,
    ) -> dict:
        """Arm a durable per-root hold — stops NEW admits; live dispatches keep running."""
        from scripts.model_manager.ui.controller.charter_runner import root_control

        return await root_control.block_root(
            root_id,
            reason=reason,
            set_by=set_by,
            unenroll=unenroll,
            clear_wip=clear_wip,
        )

    async def charter_unblock_root(
        self,
        *,
        root_id: str,
        set_by: str = "manage",
        reenroll: bool = False,
    ) -> dict:
        """Clear a durable per-root hold — BLOCKED becomes IDLE."""
        from scripts.model_manager.ui.controller.charter_runner import root_control

        return await root_control.unblock_root(
            root_id, set_by=set_by, reenroll=reenroll
        )

    async def charter_root_status(self, *, root_id: str) -> dict:
        """Read-only per-root hold snapshot without ledger writes."""
        from scripts.model_manager.ui.controller.charter_runner import root_control

        return await root_control.root_status(root_id)

    @property
    def root(self) -> Path:
        """Workspace root (read-only); fleet orchestration needs it headless."""
        return self._root

    @property
    def service_state(self) -> ServiceState:
        return self._service_state

    @property
    def restart_gate(self) -> RestartDrainGate:
        """Shared drain-aware restart gate (busy probe + per-service mutex)."""
        return self._restart_gate

    @property
    def restart_intent_store(self) -> RestartIntentStore:
        """Durable restart-intent store for the git-worker drain supervisor."""
        return self._restart_intent_store

    @property
    def shutdown_gate(self) -> ManageShutdownGate:
        """Shared quit guard (manage.sock in-flight + TUI activity tracking)."""
        return self._shutdown_gate

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
        with build_log.open("a", encoding="utf-8", errors="replace") as build_fh:
            process = await asyncio.create_subprocess_exec(
                *args,
                stdin=_DETACHED_STDIN,
                stdout=build_fh,
                stderr=asyncio.subprocess.STDOUT,
                cwd=str(self._root),
                env=env,
                start_new_session=True,
            )
            self._build_process = process
            try:
                queue: asyncio.Queue[str | object] = asyncio.Queue()
                build_log_done = object()

                async with asyncio.TaskGroup() as tg:
                    tg.create_task(
                        _pump_build_log(
                            build_log=build_log,
                            process=process,
                            queue=queue,
                            sentinel=build_log_done,
                        )
                    )
                    while True:
                        item = await queue.get()
                        if item is build_log_done:
                            break
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
            stdin=_DETACHED_STDIN,
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

        gpu_preflight_error = await asyncio.to_thread(check_gpu_docker_prerequisites)
        if gpu_preflight_error is not None:
            return f"Gateway GPU preflight failed.\n{gpu_preflight_error}"

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
        apply_checkout_code_version(env, self._root)

        result = await asyncio.create_subprocess_exec(
            "docker",
            "compose",
            "-f",
            str(compose_path),
            "up",
            "-d",
            "--force-recreate",
            stdin=_DETACHED_STDIN,
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
            "-t",
            str(_GATEWAY_STOP_TIMEOUT_S),
            container_name,
            stdin=_DETACHED_STDIN,
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
            stdin=_DETACHED_STDIN,
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
            # Popen: asyncio SubprocessTransport.close() would kill Stargate on
            # manage quit even with start_new_session=True.
            process = spawn_detached_host_process(
                [str(script), "debug"],
                cwd=self._root,
                env=env,
                log_file=log_path,
            )
        except OSError as e:
            logger.error("Failed to start Stargate subprocess: %s", e)
            return f"Failed to start Stargate: {e}"

        if process.poll() is not None:
            tail = log_path.read_text(errors="replace")[-1500:]
            return f"Stargate failed (exit {process.returncode}).\n{tail}"

        self._write_pid_file(process.pid)
        port = ServiceState.STARGATE_PORT

        def _stargate_ready() -> bool:
            return self._service_state._port_open(port)

        outcome, exit_code = await await_popen_started(process, ready=_stargate_ready)
        if outcome is StartupOutcome.CRASHED:
            tail = log_path.read_text(errors="replace")[-1500:]
            pid_path = GATEWAY_DIR / "stargate.pid"
            pid_path.unlink(missing_ok=True)
            return f"Stargate failed (exit {exit_code}).\n{tail}"
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

    async def start_mcp(self) -> str:
        """Sync workspace source into MCP and start (canonical deploy path)."""
        return await mcp_service.sync_and_restart_mcp(self._root)

    async def stop_mcp(self) -> str:
        """Stop and remove MCP server container."""
        return await mcp_service.stop_mcp(self._root)

    async def sync_restart_mcp(self, *, no_cache: bool = False) -> str:
        """Sync MCP source and restart — alias of ``start_mcp`` / fleet deploy path."""
        return await mcp_service.sync_and_restart_mcp(self._root, no_cache=no_cache)

    async def rebuild_mcp(self, *, no_cache: bool = False) -> str:
        """MCP image rebuild — ``no_cache=True`` for pip/Dockerfile changes."""
        return await mcp_service.sync_and_restart_mcp(self._root, no_cache=no_cache)

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

    async def start_git_integration_worker(self) -> str:
        """Start git-integration-worker as host TCP subprocess."""
        return await git_integration_worker_service.start_git_integration_worker(
            self._service_state, self._root, self._kill_and_wait
        )

    async def stop_git_integration_worker(self) -> str:
        """Stop git-integration-worker gracefully (extended SIGTERM budget).

        Injects the git-worker SIGTERM budget (``_git_worker_sigterm_timeout``)
        into the kill path so a managed stop gives the worker's lifespan drain room
        to finish. The wrapper is confined here — the shared uvicorn stop helper is
        untouched.
        """
        sigterm_timeout = self._git_worker_sigterm_timeout()

        async def _kill(pid: int, pid_file: Path | None, **kwargs: Any) -> str:
            kwargs.setdefault("sigterm_timeout", sigterm_timeout)
            return await self._kill_and_wait(pid, pid_file, **kwargs)

        return await git_integration_worker_service.stop_git_integration_worker(
            self._service_state, self._root, _kill
        )

    def _git_worker_sigterm_timeout(self) -> float:
        """SIGTERM wait budget for a git-integration-worker stop.

        Mirrors ``_stargate_sigterm_timeout``; defense-in-depth ONLY (the
        event-driven drain is the primary convergence mechanism). Default aligns
        with the worker lifespan drain budget so a direct/managed SIGTERM does not
        truncate the worker's own ``wait_idle``. Honors ``GIT_WORKER_SHUTDOWN_GRACE``.
        """
        env = build_service_env(self._root)
        raw = env.get("GIT_WORKER_SHUTDOWN_GRACE", "").strip()
        if not raw:
            return _DEFAULT_GIT_WORKER_SHUTDOWN_GRACE_S + _GIT_WORKER_SHUTDOWN_BUFFER_S
        try:
            grace = float(raw)
        except ValueError:
            logger.warning(
                "Invalid GIT_WORKER_SHUTDOWN_GRACE=%r; using default %.1fs",
                raw,
                _DEFAULT_GIT_WORKER_SHUTDOWN_GRACE_S,
            )
            grace = _DEFAULT_GIT_WORKER_SHUTDOWN_GRACE_S
        return max(grace, 0.0) + _GIT_WORKER_SHUTDOWN_BUFFER_S

    def build_git_worker_drain_supervisor(
        self, *, kill: Callable[[], Awaitable[str]]
    ) -> GitWorkerDrainSupervisor:
        """Construct a drain supervisor wired to the live worker + event service.

        ``kill`` is the action-appropriate terminal lifecycle: ``stop_*`` for a stop
        intent, ``restart_*`` for restart/sync_restart (host process → sync=restart).
        """
        return build_git_worker_drain_supervisor(
            self._restart_intent_store,
            worker_url=GIT_INTEGRATION_WORKER_URL,
            events_query_socket=os.environ.get(
                "EVENTS_QUERY_SOCK", "/tmp/universal-protocol/events-query.sock"
            ),
            kill=kill,
            deadline_s=_GIT_WORKER_DRAIN_DEADLINE_S,
        )

    def git_worker_kill_for(self, action: str) -> Callable[[], Awaitable[str]]:
        """Map a gated action to the worker's terminal lifecycle callable."""
        if action in ("restart", "sync_restart"):
            return self.restart_git_integration_worker
        return self.stop_git_integration_worker

    async def reconcile_pending_restart_intents(self) -> None:
        """Resume persisted restart intents and pending validations at manage boot."""
        from .restart_reconcile import reconcile_pending_restart_intents

        await reconcile_pending_restart_intents(self)

    async def restart_git_integration_worker(self) -> str:
        """Restart git-integration-worker (stop then start)."""
        await self.stop_git_integration_worker()
        return await self.start_git_integration_worker()

    async def rebuild_git_integration_worker(self, *, no_cache: bool = False) -> str:  # noqa: ARG002
        """Rebuild git-integration-worker — host process, so rebuild = restart."""
        await self.stop_git_integration_worker()
        return await self.start_git_integration_worker()

    def _cdp_ask_lifecycle_noop(self, action: str) -> str | None:
        state = cdp_ask_manage_state()
        if state == "not_enabled":
            return f"cdp-ask {action} skipped (PROJECT_ASK_URL unset)."
        if state == "disabled":
            return f"cdp-ask {action} skipped (manage lifecycle disabled)."
        return None

    def _cdp_ask_runs_local(self) -> bool:
        cfg = cdp_ask_url_config()
        if cfg is None:
            return True
        host, _port, _base = cfg
        return is_cdp_ask_local_host(host)

    async def start_cdp_ask(self) -> str:
        """Start cdp-ask locally or on the remote CDP host."""
        noop = self._cdp_ask_lifecycle_noop("start")
        if noop is not None:
            return noop
        if self._cdp_ask_runs_local():
            return await cdp_ask_service.start_cdp_ask(
                self._service_state, self._root, self._kill_and_wait
            )
        return await cdp_ask_remote.start_cdp_ask_remote(self._root)

    async def stop_cdp_ask(self) -> str:
        """Stop cdp-ask locally or on the remote CDP host."""
        noop = self._cdp_ask_lifecycle_noop("stop")
        if noop is not None:
            return noop
        if self._cdp_ask_runs_local():
            return await cdp_ask_service.stop_cdp_ask(
                self._service_state, self._root, self._kill_and_wait
            )
        return await cdp_ask_remote.stop_cdp_ask_remote(self._root)

    async def restart_cdp_ask(self) -> str:
        """Restart cdp-ask (stop then start)."""
        noop = self._cdp_ask_lifecycle_noop("restart")
        if noop is not None:
            return noop
        await self.stop_cdp_ask()
        return await self.start_cdp_ask()

    async def sync_restart_cdp_ask(self) -> str:
        """Sync cdp-ask source and restart the satellite process."""
        noop = self._cdp_ask_lifecycle_noop("sync_restart")
        if noop is not None:
            return noop
        if self._cdp_ask_runs_local():
            await self.stop_cdp_ask()
            return await self.start_cdp_ask()
        return await cdp_ask_remote.sync_restart_cdp_ask_remote(self._root)

    async def start_email_bridge(self) -> str:
        """Start email-bridge as host subprocess."""
        if _email_bridge_svc is None:
            return "email-bridge not available in this installation."
        return await _email_bridge_svc.start_email_bridge(
            self._service_state, self._root, self._kill_and_wait
        )

    async def stop_email_bridge(self) -> str:
        """Stop email-bridge gracefully."""
        if _email_bridge_svc is None:
            return "email-bridge not available in this installation."
        return await _email_bridge_svc.stop_email_bridge(
            self._service_state, self._root, self._kill_and_wait
        )

    async def restart_email_bridge(self) -> str:
        """Restart email-bridge (stop then start)."""
        await self.stop_email_bridge()
        return await self.start_email_bridge()

    async def rebuild_email_bridge(self, *, no_cache: bool = False) -> str:  # noqa: ARG002
        """Rebuild email-bridge — host process, so rebuild = restart."""
        await self.stop_email_bridge()
        return await self.start_email_bridge()

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
            "{{.State.Status}}|{{.State.Error}}",
            container_name,
            stdin=_DETACHED_STDIN,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        output = await inspect.communicate()
        raw = output[0].decode(errors="replace").strip()
        if inspect.returncode != 0:
            return f"{container_name} not found after start."
        status, _, state_error = raw.partition("|")
        status = status.lower()
        state_error = state_error.strip()
        if status in {"exited", "dead"}:
            msg = f"{container_name} exited within {wait_s:.0f}s (state: {status})."
            if state_error:
                msg += f"\n{state_error}"
            return msg
        if status == "created" and state_error:
            return f"{container_name} failed to start (state: created).\n{state_error}"
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
                stdin=_DETACHED_STDIN,
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
            stdin=_DETACHED_STDIN,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        output = await result.communicate()
        return output[0].decode(errors="replace") or "No output."

    @property
    def sidecar(self) -> SidecarController:
        return self._sidecar

    def _stargate_sigterm_timeout(self) -> float:
        """Return the SIGTERM wait budget for Stargate shutdown.

        Keep this aligned with Stargate's own service-manager cleanup window so
        manage does not force-kill the manager during normal child shutdown.
        """
        env = build_service_env(self._root)
        raw = env.get("STARGATE_SHUTDOWN_GRACE", "").strip()
        if not raw:
            return _DEFAULT_STARGATE_SHUTDOWN_GRACE_S + _STARGATE_SHUTDOWN_BUFFER_S
        try:
            grace = float(raw)
        except ValueError:
            logger.warning(
                "Invalid STARGATE_SHUTDOWN_GRACE=%r; using default %.1fs",
                raw,
                _DEFAULT_STARGATE_SHUTDOWN_GRACE_S,
            )
            grace = _DEFAULT_STARGATE_SHUTDOWN_GRACE_S
        return max(grace, 0.0) + _STARGATE_SHUTDOWN_BUFFER_S

    async def stop_stargate(self) -> str:
        """Stop Stargate regardless of whether the PID file is current.

        Three cases handled in order:
        1. PID file present and alive → SIGTERM the recorded PID.
        2. PID file absent/stale but port open → locate listener via ss(8).
        3. Port closed → nothing to do.
        """
        pid_file = GATEWAY_DIR / "stargate.pid"
        port = ServiceState.STARGATE_PORT
        sigterm_timeout = self._stargate_sigterm_timeout()

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
                return await self._kill_and_wait(
                    recorded_pid,
                    pid_file,
                    sigterm_timeout=sigterm_timeout,
                )

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
        return await self._kill_and_wait(
            listener,
            None,
            sigterm_timeout=sigterm_timeout,
        )

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

    async def probe_authority_identity(self, service: str) -> str | int | None:
        """Return manage-observed identity (host PID or container StartedAt)."""
        from .authority_identity import probe_service_identity

        return await probe_service_identity(self, service)

    def _write_pid_file(self, pid: int) -> None:
        """Writes the given PID to the Stargate PID file.

        Args:
            pid: The process ID to write.
        """
        GATEWAY_DIR.mkdir(parents=True, exist_ok=True)
        pid_path = GATEWAY_DIR / "stargate.pid"
        pid_path.write_text(str(pid) + "\n")
