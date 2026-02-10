#!/usr/bin/env python3
"""Worker process lifecycle management.

Manages worker initialization, ASGI server lifecycle, and graceful shutdown.
"""

import asyncio
import os
import signal

from process_ipc import WorkerProcess
from universal_logging import get_logger
from universal_protocol.server import app, serve

logger = get_logger(__name__)


class Worker(WorkerProcess):
    """Worker with integrated Universal Protocol ASGI server."""

    def __init__(
        self,
        worker_id: str,
        socket_path: str,
        model_id: str,
        idle_timeout: float | None = None,
    ):
        # Initialize base class
        super().__init__(worker_id, socket_path)

        # Worker state
        self.model_id = model_id
        self.universal_socket_path = socket_path
        self.asgi_server_task = None
        self.engine = None
        self.model_config = None
        self.config_received = False
        self.initialization_complete = False
        self.model_loaded = False

        # Stream idle timeout configuration
        # Priority: 1) Constructor arg, 2) Env var, 3) Default from config
        if idle_timeout is not None:
            self.idle_timeout = idle_timeout
        else:
            # Try environment variable (set by gateway)
            env_timeout = os.environ.get("STREAM_IDLE_TIMEOUT_SECONDS")
            if env_timeout:
                try:
                    self.idle_timeout = float(env_timeout)
                except ValueError:
                    logger.warning(
                        f"Invalid STREAM_IDLE_TIMEOUT_SECONDS env var: {env_timeout}, using default"
                    )
                    from universal_protocol.ws.endpoint.config import (
                        STREAM_IDLE_TIMEOUT_SECONDS,
                    )

                    self.idle_timeout = STREAM_IDLE_TIMEOUT_SECONDS
            else:
                # Fall back to library default
                from universal_protocol.ws.endpoint.config import (
                    STREAM_IDLE_TIMEOUT_SECONDS,
                )

                self.idle_timeout = STREAM_IDLE_TIMEOUT_SECONDS

        logger.info(
            f"🔧 [worker] Worker initialized for {worker_id} (idle_timeout={self.idle_timeout}s)"
        )

    async def initialize(self, socket_path: str) -> None:
        """Initialize worker and start ASGI server."""
        # Skip process_ipc server initialization
        # We'll start our own ASGI server instead

        self._logger = get_logger(f"worker.{self.worker_id}")
        self._status = {"status": "initializing"}

        # Start Universal Protocol ASGI server
        await self._start_asgi_server()

        logger.info(f"✅ Worker {self.model_id} ASGI server started on {socket_path}")
        self.initialization_complete = True

    async def _start_asgi_server(self) -> None:
        """Start the Universal Protocol ASGI server."""
        # Configure RPC handlers before starting server
        await self._configure_rpc_handlers()

        # Start server in background task
        self.asgi_server_task = asyncio.create_task(self._run_asgi_server())

        # Wait briefly for server to start
        await asyncio.sleep(0.5)

    async def _run_asgi_server(self) -> None:
        """Run the ASGI server (in background task)."""
        try:
            logger.info(f"Starting ASGI server on {self.universal_socket_path}")

            # Use the serve function from universal_protocol
            await serve(
                app=app,
                socket_path=self.universal_socket_path,
                loop="uvloop",
                log_level="info",
            )
        except Exception as e:
            logger.error(f"ASGI server error: {e}")
            raise

    async def _configure_rpc_handlers(self) -> None:
        """Register all RPC handlers including supervisor methods."""
        from universal_protocol.server.asgi_app import (
            RPC_METHODS,
            SUPERVISOR_RPC_METHODS,
        )

        # Register inference handlers (existing)
        RPC_METHODS.update(
            {
                "load_model": self.handle_load_model,
                "unload_model": self.handle_unload_model,
                "start_inference": self.handle_start_inference,
                "run_inference": self.handle_run_inference,
                "health": self.handle_health,
                "ping": self.handle_ping,
                "list_models": self.handle_list_models,
                "cancel_inference": self.handle_cancel_inference,
                "count_tokens": self.handle_count_tokens,
                "debug_stats": self.handle_debug_stats,
                "init_config": self.handle_init_config,
                "get_model_info": self.handle_get_model_info,
                "start_stream": self.handle_start_inference,
                # Whisper streaming session handlers
                "create_stream_session": self.handle_create_stream_session,
                "process_audio_chunk": self.handle_process_audio_chunk,
                "close_stream_session": self.handle_close_stream_session,
                # Whisper file transcription handler
                "transcribe_file": self.handle_transcribe_file,
                # Flux image generation handler
                "generate_image": self.handle_generate_image,
                # Embedding generation handler
                "generate_embeddings": self.handle_generate_embeddings,
            }
        )

        # Register supervisor handlers (NEW)
        supervisor_handlers = {
            "process_command": self.process_command,
            "health_check": self.handle_health,
        }

        # Only register process_data if the handler exists
        if hasattr(self, "handle_process_data"):
            supervisor_handlers["process_data"] = self.handle_process_data

        SUPERVISOR_RPC_METHODS.update(supervisor_handlers)

        logger.info(
            f"✅ Registered {len(RPC_METHODS)} inference and {len(SUPERVISOR_RPC_METHODS)} supervisor RPC handlers"
        )

    async def run(self) -> None:
        """Run the worker - wait for shutdown since ASGI server runs in background."""
        logger.info(f"🚀 Worker {self.model_id} running")

        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, self._shutdown_event.set)

        # Configure and start idle monitor (single source of truth)
        from universal_protocol.ws.registry import stream_registry

        stream_registry.configure_idle_monitor(
            timeout=self.idle_timeout,
            check_interval=self.idle_timeout / 6,
        )
        await stream_registry.start_idle_monitor()

        try:
            await self._shutdown_event.wait()
        finally:
            await stream_registry.stop_idle_monitor()
            await self.shutdown()

    async def shutdown(self) -> None:
        """Shutdown worker and ASGI server."""
        logger.info(f"Shutting down worker {self.model_id}")

        # Cancel ASGI server task
        if self.asgi_server_task and not self.asgi_server_task.done():
            self.asgi_server_task.cancel()
            try:
                await self.asgi_server_task
            except asyncio.CancelledError:
                pass

        # Clean up resources
        await self._cleanup_worker()

        # Set shutdown event
        self._shutdown_event.set()

    async def _cleanup_worker(self) -> None:
        """Override cleanup to remove socket file on shutdown.

        Called during worker shutdown to gracefully clean up resources
        including the Unix socket file created for RPC communication.
        """
        # Clean up socket file
        if hasattr(self, "universal_socket_path") and self.universal_socket_path:
            try:
                from process_ipc.utils.helpers import cleanup_socket_path

                cleanup_socket_path(self.universal_socket_path)
                logger.info(
                    f"✅ [worker] Socket cleaned up: {self.universal_socket_path}"
                )
            except Exception as e:
                logger.warning(
                    f"⚠️ [worker] Error cleaning up socket {self.universal_socket_path}: {e}"
                )
