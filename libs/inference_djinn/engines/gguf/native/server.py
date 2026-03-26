"""
Native llama-server subprocess lifecycle management.

Handles server process spawning, health monitoring, auto-recovery,
and graceful shutdown. Configuration lives in config.py.
"""

import asyncio
import os
import subprocess
import time
from enum import StrEnum
from pathlib import Path
from typing import Any

import httpx
from universal_event_bus.events.debug import emit_debug_event
from universal_logging import get_logger

from .binary import find_llama_server
from .config import ServerConfig

logger = get_logger(__name__)


class ServerStatus(StrEnum):
    """Server lifecycle status."""

    STOPPED = "stopped"
    STARTING = "starting"
    RUNNING = "running"
    UNHEALTHY = "unhealthy"
    STOPPING = "stopping"


class LlamaServerManager:
    """
    Manages llama-server subprocess lifecycle.

    Handles:
    - Server process spawning and termination
    - Health monitoring
    - Auto-recovery
    - Graceful shutdown
    """

    def __init__(self, config: ServerConfig):
        """
        Initialize server manager.

        Args:
            config: Server configuration
        """
        self.config = config
        self.process: subprocess.Popen | None = None
        self.status = ServerStatus.STOPPED
        self._health_task: asyncio.Task | None = None
        self._shutdown_event = asyncio.Event()
        self.log_path: str | None = None
        self._log_handle: Any | None = None

    @property
    def base_url(self) -> str:
        """Get display URL for logging (not for HTTP requests when using UDS)."""
        if self.config.socket_path:
            return f"unix://{self.config.socket_path}"
        return f"http://{self.config.host}:{self.config.port}"

    def _create_async_client(self, timeout: float | None = None) -> httpx.AsyncClient:
        """Create async HTTP client with appropriate transport (TCP or UDS)."""
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

    async def _emit_debug(self, step: str, **extra: Any) -> None:
        payload = {
            "step": step,
            "status": self.status.value,
            "pid": self.process.pid if self.process else None,
            "socket_path": self.config.socket_path,
            "log_path": self.log_path,
        }
        payload.update(extra)
        await emit_debug_event(
            "debug.llama.server",
            payload,
            source="gateway-worker-llama",
        )

    async def start(self, startup_timeout: float = 60.0) -> None:
        """
        Start llama-server process.

        Args:
            startup_timeout: Maximum time to wait for server to become healthy

        Raises:
            RuntimeError: If server fails to start
            TimeoutError: If server doesn't become healthy within timeout
        """
        if self.status != ServerStatus.STOPPED:
            raise RuntimeError(f"Server already {self.status}")

        logger.info("🚀 [llama-server] Starting server...")
        logger.info(f"🚀 [llama-server] Config: {self.config}")

        # Validate configuration
        self.config.validate()

        # Find llama-server binary
        binary_path = find_llama_server()
        logger.info(f"🚀 [llama-server] Binary: {binary_path}")

        # Build command
        cmd = self.config.to_cli_args()
        # Replace first element (llama-server) with full path
        cmd[0] = binary_path
        logger.info(f"🚀 [llama-server] Command: {' '.join(cmd)}")

        # Remove stale socket from previous crash (bind() fails if file exists)
        if self.config.socket_path:
            stale = Path(self.config.socket_path)
            if stale.exists():
                stale.unlink()
                logger.warning(
                    f"🧹 [llama-server] Removed stale socket: {self.config.socket_path}"
                )

        # Start process
        self.status = ServerStatus.STARTING
        child_env: dict[str, str] | None = None
        log_dir = Path("/tmp/logs/llama-server")
        log_dir.mkdir(parents=True, exist_ok=True)
        log_name = (
            Path(self.config.socket_path).stem
            if self.config.socket_path
            else f"{self.config.host}-{self.config.port}"
        )
        self.log_path = str(log_dir / f"{log_name}.log")
        self._log_handle = open(self.log_path, "a", encoding="utf-8")
        if self.config.n_gpu_layers == 0:
            # Enforce strict CPU-only execution for CUDA-enabled builds.
            child_env = os.environ.copy()
            child_env["CUDA_VISIBLE_DEVICES"] = ""
            logger.info(
                "🔒 [llama-server] CPU-only mode: hiding CUDA devices for child process"
            )
        try:
            self.process = subprocess.Popen(
                cmd,
                stdout=self._log_handle,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                env=child_env,
            )
            logger.info(f"🚀 [llama-server] Process started (PID: {self.process.pid})")
            await self._emit_debug(
                "process_started",
                command=cmd,
                verbose=self.config.verbose,
            )
        except Exception as e:
            self.status = ServerStatus.STOPPED
            if self._log_handle:
                self._log_handle.close()
                self._log_handle = None
            raise RuntimeError(f"Failed to start llama-server: {e}") from e

        # Wait for server to become healthy
        try:
            await self._wait_for_health(startup_timeout)
        except TimeoutError:
            logger.error("❌ [llama-server] Server failed to become healthy")
            await self.stop()
            raise

        # Verify inference readiness (health ≠ ready for requests)
        try:
            await self._wait_for_inference_ready()
        except TimeoutError:
            logger.error("❌ [llama-server] Server healthy but not inference-ready")
            await self.stop()
            raise

        self.status = ServerStatus.RUNNING
        logger.info("✅ [llama-server] Server is running and inference-ready")
        await self._emit_debug("server_running")

        # Start health monitoring
        self._health_task = asyncio.create_task(self._monitor_health())

    async def _wait_for_health(self, timeout: float) -> None:
        """
        Wait for server to become healthy.

        Args:
            timeout: Maximum time to wait

        Raises:
            TimeoutError: If server doesn't become healthy within timeout
        """
        start_time = time.time()
        async with self._create_async_client(timeout=2.0) as client:
            while time.time() - start_time < timeout:
                try:
                    response = await client.get("/health")
                    if response.status_code == 200:
                        data = response.json()
                        if data.get("status") == "ok":
                            logger.info("✅ [llama-server] Health check passed")
                            return
                except (httpx.RequestError, httpx.HTTPStatusError):
                    pass

                self._check_process_alive()
                await asyncio.sleep(1.0)

        raise TimeoutError(f"Server failed to become healthy within {timeout}s")

    async def _wait_for_inference_ready(
        self,
        timeout: float = 10.0,
        max_attempts: int = 20,
    ) -> None:
        """
        Wait for server to accept inference requests (not just /health).

        llama-server can return 200 on /health but still 503 on inference
        endpoints for ~50-100ms after startup. This probe catches that gap.

        Args:
            timeout: Per-request timeout
            max_attempts: Maximum probe attempts before giving up

        Raises:
            TimeoutError: If server doesn't become inference-ready
        """
        if self.config.embedding:
            endpoint = "/v1/embeddings"
            body: dict[str, Any] = {"input": ["ready"]}
        else:
            endpoint = "/v1/completions"
            body = {"prompt": "hi", "max_tokens": 1}

        logger.info(f"🔍 [llama-server] Probing inference readiness via {endpoint}...")

        async with self._create_async_client(timeout=timeout) as client:
            for attempt in range(1, max_attempts + 1):
                try:
                    response = await client.post(
                        endpoint,
                        json=body,
                    )
                    if response.status_code == 200:
                        logger.info(
                            f"✅ [llama-server] Inference ready (attempt {attempt})"
                        )
                        return
                    # 503 = server not ready yet; keep trying
                    logger.debug(
                        f"🔍 [llama-server] Readiness probe {attempt}/{max_attempts}: "
                        f"HTTP {response.status_code}"
                    )
                except httpx.RequestError as e:
                    logger.debug(
                        f"🔍 [llama-server] Readiness probe {attempt}/{max_attempts}: "
                        f"{e}"
                    )

                self._check_process_alive()
                await asyncio.sleep(0.5)

        raise TimeoutError(f"Server not inference-ready after {max_attempts} attempts")

    def _check_process_alive(self) -> None:
        """Raise RuntimeError if the server process has exited."""
        if self.process and self.process.poll() is not None:
            stdout, stderr = self.process.communicate(timeout=1.0)
            error_msg = f"Server process died with exit code {self.process.returncode}"
            if stderr:
                logger.error(f"❌ [llama-server] stderr: {stderr}")
                error_msg += f"\nstderr: {stderr}"
            if stdout:
                logger.error(f"❌ [llama-server] stdout: {stdout}")
                error_msg += f"\nstdout: {stdout}"
            raise RuntimeError(error_msg)

    async def _monitor_health(self) -> None:
        """Monitor server health and handle failures."""
        logger.info("🔍 [llama-server] Starting health monitoring")

        while not self._shutdown_event.is_set():
            try:
                async with self._create_async_client(timeout=5.0) as client:
                    response = await client.get("/health")
                    if response.status_code != 200:
                        logger.warning(
                            f"⚠️ [llama-server] Health check failed: {response.status_code}"
                        )
                        self.status = ServerStatus.UNHEALTHY
                    elif self.status == ServerStatus.UNHEALTHY:
                        logger.info("✅ [llama-server] Server recovered")
                        self.status = ServerStatus.RUNNING

            except httpx.RequestError as e:
                logger.warning(f"⚠️ [llama-server] Health check error: {e}")
                self.status = ServerStatus.UNHEALTHY

            # Check if process died
            if self.process and self.process.poll() is not None:
                exit_code = self.process.returncode
                # Capture any error output
                try:
                    stdout, stderr = self.process.communicate(timeout=1.0)
                    logger.error(
                        f"❌ [llama-server] Process died with exit code {exit_code}"
                    )
                    if stderr:
                        logger.error(f"❌ [llama-server] stderr: {stderr}")
                    if stdout:
                        logger.error(f"❌ [llama-server] stdout: {stdout}")
                except Exception as e:
                    logger.error(f"❌ [llama-server] Failed to capture output: {e}")
                self.status = ServerStatus.STOPPED
                break

            await asyncio.sleep(10.0)

        logger.info("🔍 [llama-server] Health monitoring stopped")

    async def stop(self, timeout: float = 30.0) -> None:
        """
        Stop llama-server process.

        Args:
            timeout: Maximum time to wait for graceful shutdown
        """
        if self.status == ServerStatus.STOPPED:
            return

        logger.info("🛑 [llama-server] Stopping server...")
        self.status = ServerStatus.STOPPING
        self._shutdown_event.set()

        # Stop health monitoring
        if self._health_task:
            self._health_task.cancel()
            try:
                await self._health_task
            except asyncio.CancelledError:
                pass

        # Terminate process
        if self.process:
            try:
                self.process.terminate()
                try:
                    await asyncio.wait_for(
                        asyncio.to_thread(self.process.wait),
                        timeout=timeout,
                    )
                    logger.info("✅ [llama-server] Server stopped gracefully")
                except TimeoutError:
                    logger.warning(
                        "⚠️ [llama-server] Graceful shutdown timed out, killing"
                    )
                    self.process.kill()
                    await asyncio.to_thread(self.process.wait)
                    logger.info("✅ [llama-server] Server killed")
            except Exception as e:
                logger.error(f"❌ [llama-server] Error stopping server: {e}")

        self.process = None
        self.status = ServerStatus.STOPPED
        await self._emit_debug("server_stopped")
        if self._log_handle:
            self._log_handle.close()
            self._log_handle = None

        # Clean up Unix socket file
        if self.config.socket_path:
            socket_file = Path(self.config.socket_path)
            if socket_file.exists():
                socket_file.unlink()
                logger.info(
                    f"🧹 [llama-server] Cleaned up socket: {self.config.socket_path}"
                )

    async def __aenter__(self):
        """Async context manager entry."""
        await self.start()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit."""
        await self.stop()
