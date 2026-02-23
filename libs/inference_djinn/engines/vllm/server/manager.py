"""
vLLM server subprocess lifecycle management.

Handles vllm serve process spawning, health monitoring, and graceful shutdown.
"""

import asyncio
import shutil
import subprocess
import time
from enum import StrEnum
from pathlib import Path
from typing import Any

import httpx
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

    def __init__(self, config: VLLMServerConfig) -> None:
        self.config = config
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
            self.process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                env=env,
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
        """Background health check every 10s."""
        logger.info("🔍 [vllm-server] Starting health monitoring")
        while not self._shutdown_event.is_set():
            try:
                async with self._create_async_client(timeout=5.0) as client:
                    response = await client.get("/health")
                    if response.status_code != 200:
                        self.status = ServerStatus.UNHEALTHY
                    elif self.status == ServerStatus.UNHEALTHY:
                        self.status = ServerStatus.RUNNING
            except httpx.RequestError:
                self.status = ServerStatus.UNHEALTHY

            if self.process and self.process.poll() is not None:
                try:
                    self.process.communicate(timeout=1.0)
                except subprocess.TimeoutExpired:
                    pass
                self.status = ServerStatus.STOPPED
                break
            await asyncio.sleep(10.0)
        logger.info("🔍 [vllm-server] Health monitoring stopped")

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
                self.process.terminate()
                try:
                    await asyncio.wait_for(
                        asyncio.to_thread(self.process.wait),
                        timeout=timeout,
                    )
                    logger.info("✅ [vllm-server] Server stopped gracefully")
                except TimeoutError:
                    logger.warning(
                        "⚠️ [vllm-server] Graceful shutdown timed out, killing"
                    )
                    self.process.kill()
                    await asyncio.to_thread(self.process.wait)
                    logger.info("✅ [vllm-server] Server killed")
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
