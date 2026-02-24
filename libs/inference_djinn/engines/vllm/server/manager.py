"""
vLLM server subprocess lifecycle management.

Handles vllm serve process spawning, health monitoring, and graceful shutdown.
"""

import asyncio
import os
import shutil
import signal
import subprocess
import time
from collections.abc import Awaitable, Callable
from enum import StrEnum
from pathlib import Path
from typing import Any

import httpx
from process_ipc.utils import setup_parent_death_signal
from universal_logging import get_logger

from .config import VLLMServerConfig

logger = get_logger(__name__)


def build_vllm_command(config: VLLMServerConfig) -> list[str]:
    """Build vllm serve command, resolving the vllm binary.

    Tries shutil.which("vllm") first, falls back to
    python -m vllm.entrypoints.cli.main (the full CLI that supports
    all subcommands and flags like --task for embedding models).
    """
    args = config.to_cli_args()  # ["vllm", "serve", model, ...]
    vllm_path = shutil.which("vllm")
    if vllm_path:
        args[0] = vllm_path
        return args
    py = shutil.which("python3") or shutil.which("python")
    if py:
        # Use the CLI entrypoint which supports "serve" subcommand and all
        # flags (--task, --enable-auto-tool-choice, etc.). args[1:] keeps
        # ["serve", model, ...] intact.
        return [py, "-m", "vllm.entrypoints.cli.main", *args[1:]]
    return args


class ServerStatus(StrEnum):
    """Server lifecycle status."""

    STOPPED = "stopped"
    STARTING = "starting"
    RUNNING = "running"
    UNHEALTHY = "unhealthy"
    STOPPING = "stopping"


class VLLMServerManager:
    """
    Manages vllm serve subprocess lifecycle.

    Handles:
    - Server process spawning and termination
    - Health monitoring
    - Graceful shutdown
    """

    def __init__(
        self,
        config: VLLMServerConfig,
        *,
        on_server_crashed: Callable[[], Awaitable[None]] | None = None,
        on_server_recovered: Callable[[], Awaitable[None]] | None = None,
    ) -> None:
        self.config = config
        self._on_server_crashed = on_server_crashed
        self._on_server_recovered = on_server_recovered
        self._startup_timeout = 600.0
        self.process: subprocess.Popen | None = None
        self.status = ServerStatus.STOPPED
        self._health_task: asyncio.Task | None = None
        self._shutdown_event = asyncio.Event()

    @property
    def base_url(self) -> str:
        """Get display URL for logging."""
        if self.config.socket_path:
            return f"unix://{self.config.socket_path}"
        return f"http://{self.config.host}:{self.config.port}"

    def _create_async_client(self, timeout: float | None = None) -> httpx.AsyncClient:
        """Create async HTTP client (UDS or TCP)."""
        kwargs: dict[str, Any] = {}
        if timeout is not None:
            kwargs["timeout"] = timeout
        if self.config.socket_path:
            transport = httpx.AsyncHTTPTransport(uds=self.config.socket_path)
            return httpx.AsyncClient(
                transport=transport,
                base_url="http://localhost",
                **kwargs,
            )
        return httpx.AsyncClient(
            base_url=f"http://{self.config.host}:{self.config.port}",
            **kwargs,
        )

    def _build_cmd(self) -> list[str]:
        """Build command list from config CLI args, resolving the vllm binary."""
        return build_vllm_command(self.config)

    async def start(self, startup_timeout: float = 600.0) -> None:
        """
        Start vllm serve process.

        Args:
            startup_timeout: Maximum time to wait for server to become healthy

        Raises:
            RuntimeError: If server fails to start
            TimeoutError: If server doesn't become healthy within timeout
        """
        if self.status != ServerStatus.STOPPED:
            raise RuntimeError(f"Server already {self.status}")

        logger.info("🚀 [vllm-server] Starting server...")
        self.config.validate()
        self._startup_timeout = startup_timeout

        cmd = self._build_cmd()
        logger.info(f"🚀 [vllm-server] Command: {' '.join(cmd)}")

        if self.config.socket_path:
            stale = Path(self.config.socket_path)
            if stale.exists():
                stale.unlink()
                logger.warning(
                    f"🧹 [vllm-server] Removed stale socket: {self.config.socket_path}"
                )

        self.status = ServerStatus.STARTING
        env = self.config.to_subprocess_env()
        try:
            # start_new_session=True places the vLLM HTTP server and all its
            # children (EngineCore, resource tracker, etc.) in a dedicated
            # process group. This lets us kill the full tree via os.killpg
            # without affecting the parent worker process.
            self.process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                env=env,
                start_new_session=True,
                preexec_fn=setup_parent_death_signal,
            )
            logger.info(f"🚀 [vllm-server] Process started (PID: {self.process.pid})")
        except Exception as e:
            self.status = ServerStatus.STOPPED
            raise RuntimeError(f"Failed to start vllm serve: {e}") from e

        try:
            await self._wait_for_health(startup_timeout)
        except TimeoutError:
            logger.error("❌ [vllm-server] Server failed to become healthy")
            await self.stop()
            raise

        self.status = ServerStatus.RUNNING
        logger.info("✅ [vllm-server] Server is running")
        self._health_task = asyncio.create_task(self._monitor_health())

    async def _wait_for_health(self, timeout: float) -> None:
        """Poll GET /health until server returns 200."""
        start_time = time.time()
        async with self._create_async_client(timeout=10.0) as client:
            while time.time() - start_time < timeout:
                try:
                    response = await client.get("/health")
                    if response.status_code == 200:
                        logger.info("✅ [vllm-server] Health check passed")
                        return
                except (httpx.RequestError, httpx.HTTPStatusError):
                    pass
                self._check_process_alive()
                await asyncio.sleep(2.0)
        raise TimeoutError(f"Server failed to become healthy within {timeout}s")

    def _kill_process_group(self) -> None:
        """Kill the vLLM process group (HTTP server + EngineCore + all children).

        # ∀ process in group: receives SIGKILL immediately.
        # Falls back to self.process.kill() if the pgid cannot be resolved
        # (e.g. process already exited before this call).
        """
        if not self.process:
            return
        try:
            pgid = os.getpgid(self.process.pid)
            os.killpg(pgid, signal.SIGKILL)
            logger.info(
                "🔪 [vllm-server] Killed process group %d (HTTP server + EngineCore)",
                pgid,
            )
        except ProcessLookupError:
            # Process already gone — nothing to kill.
            pass
        except OSError as e:
            logger.warning(
                "⚠️ [vllm-server] killpg failed (%s), falling back to process.kill()",
                e,
            )
            try:
                self.process.kill()
            except OSError:
                pass

    def _check_process_alive(self) -> None:
        """Raise RuntimeError if the server process has exited."""
        if self.process and self.process.poll() is not None:
            try:
                stdout, stderr = self.process.communicate(timeout=1.0)
            except subprocess.TimeoutExpired:
                stdout, stderr = "", ""
            error_msg = f"Server process died with exit code {self.process.returncode}"
            if stderr:
                logger.error(f"❌ [vllm-server] stderr: {stderr}")
                error_msg += f"\nstderr: {stderr}"
            if stdout:
                logger.error(f"❌ [vllm-server] stdout: {stdout}")
                error_msg += f"\nstdout: {stdout}"
            raise RuntimeError(error_msg)

    async def _monitor_health(self) -> None:
        """Background health check every 10s.

        # status = STOPPED  ⟺ process exited (or forcibly killed as hung)
        # status = UNHEALTHY ⟺ server reachable but returns non-200
        # httpx.RequestError with process alive → server not responding;
        #   ∀ VLLM_SLEEP_WHEN_IDLE: /health may time out while compute is suspended
        #   without the process being dead — do not change status transiently.
        #   ∀ consecutive_unreachable ≥ _HUNG_THRESHOLD: process is treated as
        #   hung (frozen event loop), killed, and status set to STOPPED so the
        #   engine lifecycle can restart it.
        """
        # 6 × 10s = 60s before treating an unresponsive-but-alive process as hung.
        # 60s aligns with VLLM_ENGINE_ITERATION_TIMEOUT_S default and with
        # VLLM_SLEEP_WHEN_IDLE=0 there is no idle->active wake-up delay.
        hung_threshold = 6
        consecutive_unreachable = 0

        logger.info("🔍 [vllm-server] Starting health monitoring")
        while not self._shutdown_event.is_set():
            try:
                async with self._create_async_client(timeout=5.0) as client:
                    response = await client.get("/health")
                    consecutive_unreachable = 0
                    if response.status_code != 200:
                        if self.status != ServerStatus.UNHEALTHY:
                            logger.warning(
                                "⚠️ [vllm-server] Marking UNHEALTHY: /health returned %d",
                                response.status_code,
                            )
                            self.status = ServerStatus.UNHEALTHY
                    elif self.status == ServerStatus.UNHEALTHY:
                        logger.info("✅ [vllm-server] Health recovered")
                        self.status = ServerStatus.RUNNING
            except httpx.RequestError as e:
                if self.process and self.process.poll() is None:
                    consecutive_unreachable += 1
                    if consecutive_unreachable >= hung_threshold:
                        logger.error(
                            "❌ [vllm-server] Unresponsive for %ds with process alive "
                            "— event loop likely frozen, killing process group",
                            consecutive_unreachable * 10,
                        )
                        self._kill_process_group()
                        self.process = None
                        if self._on_server_crashed:
                            await self._on_server_crashed()
                        try:
                            await self._restart_server()
                            if self._on_server_recovered:
                                await self._on_server_recovered()
                            consecutive_unreachable = 0
                            continue
                        except Exception as restart_error:
                            logger.error(
                                "❌ [vllm-server] Auto-restart failed: %s",
                                restart_error,
                            )
                            self.status = ServerStatus.STOPPED
                            break
                    logger.debug(
                        "🔍 [vllm-server] /health unreachable (process alive, "
                        "may be sleeping; %d/%d): %s",
                        consecutive_unreachable,
                        hung_threshold,
                        e,
                    )

            if self.process and self.process.poll() is not None:
                try:
                    self.process.communicate(timeout=1.0)
                except subprocess.TimeoutExpired:
                    pass
                self._kill_process_group()
                self.process = None
                if self._on_server_crashed:
                    await self._on_server_crashed()
                try:
                    await self._restart_server()
                    if self._on_server_recovered:
                        await self._on_server_recovered()
                    consecutive_unreachable = 0
                    continue
                except Exception as restart_error:
                    logger.error(
                        "❌ [vllm-server] Auto-restart failed: %s",
                        restart_error,
                    )
                    self.status = ServerStatus.STOPPED
                    break
            await asyncio.sleep(10.0)
        logger.info("🔍 [vllm-server] Health monitoring stopped")

    async def _restart_server(self) -> None:
        """Respawn vLLM server process after crash.

        Raises:
            RuntimeError: If respawn fails
            TimeoutError: If server doesn't become healthy
        """
        self.process = None
        self.status = ServerStatus.STARTING

        # ∀ restart: stale socket from the killed process must be removed
        # before spawning; vLLM's sock.bind() raises EADDRINUSE otherwise.
        if self.config.socket_path:
            stale = Path(self.config.socket_path)
            if stale.exists():
                stale.unlink()
                logger.info(
                    "🧹 [vllm-server] Removed stale socket before restart: %s",
                    self.config.socket_path,
                )

        cmd = self._build_cmd()
        env = self.config.to_subprocess_env()
        self.process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=env,
            start_new_session=True,
            preexec_fn=setup_parent_death_signal,
        )
        logger.info(
            "🔄 [vllm-server] Respawned process (PID: %d), waiting for health...",
            self.process.pid,
        )
        await self._wait_for_health(self._startup_timeout)
        self.status = ServerStatus.RUNNING

    async def stop(self, timeout: float = 30.0) -> None:
        """Stop vllm serve process and clean up UDS if used."""
        if self.status == ServerStatus.STOPPED:
            return

        logger.info("🛑 [vllm-server] Stopping server...")
        self.status = ServerStatus.STOPPING
        self._shutdown_event.set()

        if self._health_task:
            self._health_task.cancel()
            try:
                await self._health_task
            except asyncio.CancelledError:
                pass

        if self.process:
            try:
                # SIGTERM to the process group so EngineCore and APIServer
                # workers also receive the signal.
                try:
                    pgid = os.getpgid(self.process.pid)
                    os.killpg(pgid, signal.SIGTERM)
                except OSError:
                    self.process.terminate()
                try:
                    await asyncio.wait_for(
                        asyncio.to_thread(self.process.wait),
                        timeout=timeout,
                    )
                    logger.info("✅ [vllm-server] Server stopped gracefully")
                except TimeoutError:
                    logger.warning(
                        "⚠️ [vllm-server] Graceful shutdown timed out, killing process group"
                    )
                # Always SIGKILL the process group after the leader exits.
                # vLLM's multiprocessing workers (EngineCore, APIServer_*)
                # stay in the same group and may survive SIGTERM. Waiting only
                # for self.process (the leader) is not sufficient — workers
                # hold GPU memory and will leak it if left running.
                self._kill_process_group()
                await asyncio.to_thread(self.process.wait)
                logger.info("✅ [vllm-server] Process group killed")
            except Exception as e:
                logger.error(f"❌ [vllm-server] Error stopping server: {e}")

        self.process = None
        self.status = ServerStatus.STOPPED

        if self.config.socket_path:
            socket_file = Path(self.config.socket_path)
            if socket_file.exists():
                socket_file.unlink()
                logger.info(
                    f"🧹 [vllm-server] Cleaned up socket: {self.config.socket_path}"
                )
