"""ProcessCommunicationManager for worker orchestration."""

from pathlib import Path
from typing import Any

from process_ipc import ProcessSupervisor, UnixSocketConfig
from universal_logging import get_logger

from ....errors import (
    ModelLoadingError,
    SyntaxErrorException,
    WorkerInitializationError,
)
from ...utils import cleanup_socket_file as cleanup_socket_file_util
from ...utils import get_universal_protocol_socket_path
from .config_builder import build_model_config_for_worker
from .orchestration import (
    execute_model_loading_flow,
    handle_general_exception,
    handle_load_failure,
    handle_syntax_error_exception,
)
from .rpc_client import check_rpc_health

logger = get_logger(__name__)


class ProcessCommunicationManager:
    """
    Manager for worker process communication.

    Orchestrates model loading across config building, RPC communication,
    health validation, and cleanup.

    Invariant: ∀ model_load: config_build → rpc_call → health_check → (success ∨ cleanup)
    """

    def __init__(
        self,
        state: Any,
        ipc_socket_dir: Path,
        gateway_config: Any,
        model_registry: Any,
    ):
        """
        Initialize communication manager.

        Args:
            state: Shared process state container
            ipc_socket_dir: Directory for IPC socket files
            gateway_config: Gateway configuration object
            model_registry: Model registry for getting model configurations
        """
        self.state = state
        self.ipc_socket_dir = ipc_socket_dir
        self.gateway_config = gateway_config
        self.model_registry = model_registry

    def create_transport_config(self, socket_file_path: str) -> UnixSocketConfig:
        """
        Create transport configuration with standard settings.

        Args:
            socket_file_path: Path to the Unix socket file

        Returns:
            UnixSocketConfig: Configured transport settings
        """
        return UnixSocketConfig(
            socket_path=socket_file_path,
            timeout=30.0,
            retry_attempts=3,
            socket_permissions=0o600,
            backlog=5,
            max_message_size=10 * 1024 * 1024,  # 10 MiB for large tokenization
        )

    async def _check_rpc_health(
        self, socket_path: str, model_id: str, timeout: float = 5.0
    ) -> bool:
        """
        Check RPC health (delegates to rpc_client module).

        Args:
            socket_path: Path to worker socket
            model_id: Model identifier
            timeout: Health check timeout

        Returns:
            True if healthy, False otherwise
        """
        return await check_rpc_health(socket_path, model_id, timeout)

    async def send_model_config(
        self,
        model_id: str,
        supervisor: ProcessSupervisor,
        correlation_id: str | None = None,
    ) -> dict[str, Any] | None:
        """
        Send model configuration to worker process using RPC (event-driven).

        Orchestrates:
        1. Failed worker check
        2. Config building (from registry)
        3. Preflight checks (worker status, RPC health)
        4. Init config command
        5. Load model command
        6. Response validation
        7. Cleanup on failure (via events)

        Args:
            model_id: Model identifier
            supervisor: ProcessSupervisor instance
            correlation_id: Request correlation ID (optional)

        Returns:
            dict with keys: {'success': bool, 'context_size': int} on success
            None on failure

        Raises:
            WorkerInitializationError: When worker initialization fails
            ModelLoadingError: When model loading fails
            SyntaxErrorException: When syntax error is detected
        """
        # Check if this worker is in our failed workers list
        if model_id in self.state.failed_workers:
            logger.error(
                f"❌ Worker {model_id} is marked as failed, cannot send config"
            )
            raise WorkerInitializationError(
                message=f"Worker {model_id} is marked as failed",
                internal_error="Worker previously failed and is marked as unavailable",
                context={
                    "operation": "model_config_send",
                    "model_id": model_id,
                    "component": "worker_manager",
                    "worker_status": "failed",
                },
            )

        # Validate model registry available
        if not self.model_registry:
            raise WorkerInitializationError(
                message="Model registry not available",
                internal_error="Model registry dependency not initialized",
                context={
                    "operation": "model_config_send",
                    "model_id": model_id,
                    "component": "worker_manager",
                },
            )

        # Get socket path for cleanup events
        socket_path = get_universal_protocol_socket_path(model_id)

        try:
            # Execute main loading flow
            success, context_size, error_msg = await execute_model_loading_flow(
                model_id,
                supervisor,
                self.model_registry,
                self.gateway_config,
                correlation_id,
            )

            if not success:
                # Get config for error context
                config_to_send = build_model_config_for_worker(
                    model_id,
                    self.model_registry,
                    self.gateway_config,
                )

                await handle_load_failure(
                    model_id,
                    supervisor,
                    self.gateway_config,
                    socket_path,
                    self.state.failed_workers,
                    error_msg,
                    config_to_send,
                )

            # Validate context_size is present (only for LLM models)
            # Non-LLM models (e.g., Whisper, Flux) don't have context_size
            engine_type = None
            if self.model_registry:
                model_config = self.model_registry.get_model_config(model_id)
                if model_config:
                    # Check info.engine (converted catalog structure)
                    info = model_config.get("info", {})
                    if isinstance(info, dict):
                        engine_type = info.get("engine")

            if context_size is None and engine_type not in (
                "faster-whisper",
                "diffusers",
            ):
                logger.error(
                    f"❌ Worker did not return context_size for {model_id}. "
                    + "Catalog metadata must include max_model_len."
                )
                raise WorkerInitializationError(
                    message=f"Missing context_size in worker response for {model_id}",
                    internal_error="Worker did not return context_size from catalog metadata",
                    stack_trace=None,
                    context={
                        "operation": "model_load_verification",
                        "model_id": model_id,
                        "component": "worker_manager",
                    },
                )

            # Success
            if context_size is not None:
                logger.info(
                    f"✅ Model {model_id} loaded successfully (context: {context_size})"
                )
            else:
                logger.info(
                    f"✅ Model {model_id} loaded successfully "
                    f"(engine={engine_type}, no context_size)"
                )
            self.state.failed_workers.discard(model_id)

            result = {"success": True}
            if context_size is not None:
                result["context_size"] = context_size
            return result

        except (WorkerInitializationError, ModelLoadingError, SyntaxErrorException):
            # Re-raise enhanced errors as-is
            raise

        except SyntaxError as e:
            await handle_syntax_error_exception(
                model_id,
                supervisor,
                self.gateway_config,
                socket_path,
                self.state.failed_workers,
                e,
            )

        except Exception as e:
            await handle_general_exception(
                model_id,
                supervisor,
                self.gateway_config,
                socket_path,
                self.state.failed_workers,
                e,
            )

    def handle_transport_error(
        self, error_message: str, model_id: str, context: str = "operation"
    ) -> None:
        """
        Handle transport/connection errors consistently across all operations.

        Args:
            error_message: The error message to check
            model_id: Model ID for logging context
            context: Operation context (e.g., "streaming", "inference")

        Raises:
            RuntimeError: If the error is a transport/connection error
        """
        from ...errors import is_connection_error

        if is_connection_error(error_message):
            logger.error(
                f"🚨 [manager] Transport error during {context} for {model_id}: {error_message}"
            )
            raise RuntimeError(f"Worker connection failed: {error_message}")
        elif "timed out" in error_message.lower():
            logger.error(
                f"⏰ [manager] Timeout during {context} for {model_id}: {error_message}"
            )
            raise RuntimeError(f"Operation timed out: {error_message}")

    def get_socket_path(self, model_id: str) -> str:
        """
        Get socket path for a model.

        Args:
            model_id: Model ID

        Returns:
            Socket file path for the model
        """
        # First check if we have a stored socket path (Universal Protocol)
        stored_path = self.state.get_socket_path(model_id)
        if stored_path:
            return stored_path

        # Fallback to Universal Protocol convention (per MVP)
        return get_universal_protocol_socket_path(model_id)

    async def cleanup_socket_file(self, model_id: str) -> None:
        """Clean up orphaned socket files."""
        try:
            socket_path = self.get_socket_path(model_id)
            cleanup_socket_file_util(socket_path)
            logger.info(f"🧹 Cleaned up socket file for {model_id}")
        except Exception as e:
            logger.warning(f"⚠️ Error cleaning up socket file for {model_id}: {e}")
