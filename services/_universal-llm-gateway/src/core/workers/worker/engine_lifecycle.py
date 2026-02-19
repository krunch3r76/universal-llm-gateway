"""Engine lifecycle management for worker.

Handles model engine loading and initialization using the factory pattern.
"""

from typing import Any

from universal_logging import format_json_for_log, get_logger

from ..engine_factory import (
    EngineFactory,
    EngineInitializationError,
    EngineTimeoutError,
)

logger = get_logger(__name__)


def _validate_gpu_requirement(
    engine_type: str, loader_config: dict[str, Any], model_id: str
) -> tuple[bool, str]:
    """
    Validate GPU requirement for engine and fail-fast if unavailable.

    Returns:
        (requires_gpu, requirement_reason)

    Raises:
        RuntimeError: If GPU required but unavailable (fail-fast)
    """
    requires_gpu = False
    gpu_requirement_reason = ""

    # Determine GPU requirement by engine type
    if engine_type == "native":
        n_gpu_layers = loader_config.get("n_gpu_layers")
        if n_gpu_layers is None:
            raise ValueError(
                f"Model {model_id} ({engine_type}/GGUF) missing n_gpu_layers "
                f"in loader_config. GGUF models must specify n_gpu_layers "
                f"(-1=GPU, 0=CPU, >0=HYBRID)."
            )
        if n_gpu_layers != 0:
            requires_gpu = True
            gpu_requirement_reason = f"n_gpu_layers={n_gpu_layers}"

    elif engine_type == "vllm":
        requires_gpu = True
        gpu_requirement_reason = f"engine={engine_type} (vLLM requires GPU)"

    elif engine_type == "diffusers":
        requires_gpu = True
        gpu_requirement_reason = f"engine={engine_type} (diffusers requires GPU)"

    elif engine_type == "faster-whisper":
        device = loader_config.get("device", "auto")
        if device != "cpu":  # "cuda" or "auto"
            requires_gpu = True
            gpu_requirement_reason = f"device={device}"

    if not requires_gpu:
        return False, ""

    # Validate GPU availability by engine type
    if engine_type == "native":
        from src.core.gpu_detection import GPUCapabilities

        if not GPUCapabilities.is_llama_server_available():
            error_msg = (
                f"❌ FAIL-FAST: Cannot load model '{model_id}' - "
                f"GPU required but llama-server binary not found\n"
                f"   Model config: {gpu_requirement_reason}\n"
                f"   Resolution: Ensure llama-server is installed at "
                f"/opt/llama-server/bin or on PATH."
            )
            logger.error(error_msg)
            raise RuntimeError(error_msg)

        if not GPUCapabilities.is_hardware_gpu_available():
            error_msg = (
                f"❌ FAIL-FAST: Cannot load model '{model_id}' - "
                f"GPU required but no GPU hardware detected\n"
                f"   Model config: {gpu_requirement_reason}\n"
                f"   Resolution: Ensure GPU is accessible (check nvidia-smi)."
            )
            logger.error(error_msg)
            raise RuntimeError(error_msg)

        logger.info(
            f"✅ [worker] GPU validation passed - llama-server binary present "
            f"and hardware GPU available for {gpu_requirement_reason}"
        )

    else:  # vllm, diffusers, faster-whisper all require PyTorch
        try:
            import torch

            if not torch.cuda.is_available():
                error_msg = (
                    f"❌ FAIL-FAST: Cannot load model '{model_id}' - "
                    f"GPU required but CUDA unavailable\n"
                    f"   Model config: {gpu_requirement_reason}\n"
                    f"   This check prevents silent CPU fallback which would "
                    f"cause severe performance degradation.\n"
                    f"   Resolution: Ensure GPU is accessible or restart the container."
                )
                logger.error(error_msg)
                raise RuntimeError(error_msg)
            else:
                logger.info(
                    f"✅ [worker] GPU validation passed - CUDA available "
                    f"for {gpu_requirement_reason}"
                )
        except ImportError:
            error_msg = (
                f"❌ FAIL-FAST: Cannot load model '{model_id}' - "
                f"GPU required but PyTorch not available\n"
                f"   Model config: {gpu_requirement_reason}\n"
                f"   Resolution: Ensure PyTorch is installed with CUDA support."
            )
            logger.error(error_msg)
            raise RuntimeError(error_msg)

    return True, gpu_requirement_reason


class EngineLifecycle:
    """Mix-in class providing engine lifecycle methods."""

    # Assumes self.model_config, self.engine, self.model_loaded, self.model_id exist

    def _on_engine_crash(self, exit_code: int) -> None:
        """Handle engine crash notification — reset worker state."""
        logger.error(
            "❌ [worker] Engine process crashed (exit_code=%s), marking unloaded",
            exit_code,
        )
        self.model_loaded = False

    async def _load_model_engine(self) -> None:
        """Load the appropriate model engine based on config using the factory."""
        # Remove import - truncation now automatic

        if not self.model_config:
            raise RuntimeError("No model config available")

        engine_type = self.model_config.get("engine", "").lower()
        model_format = self.model_config.get("format", "").lower()
        model_path = self.model_config.get("path", "")
        loader_config = self.model_config.get("loader_config", {})

        # Resolve model path relative to MODEL_PATH_ROOT
        model_path = self._resolve_model_path(model_path)

        # Resolve clip_model_path for vision models (if present)
        # Now handles both relative and legacy absolute paths
        if "clip_model_path" in loader_config:
            clip_path = loader_config["clip_model_path"]
            if clip_path:
                # Copy dict before mutation to avoid affecting cached config
                loader_config = loader_config.copy()
                loader_config["clip_model_path"] = self._resolve_model_path(clip_path)
                logger.info(
                    "🔧 [worker] Resolved clip_model_path: %s",
                    loader_config["clip_model_path"],
                )

        logger.info(
            f"🔧 [worker] Loading {engine_type} engine "
            f"(format={model_format}) from {model_path}"
        )
        logger.info(f"🔧 [worker] Loader config: {format_json_for_log(loader_config)}")

        # Fail-fast GPU validation: Prevent silent CPU fallback
        _validate_gpu_requirement(engine_type, loader_config, self.model_id)

        try:
            # Use factory to create and load engine with proper timeout handling
            self.engine = await EngineFactory.create_and_load(
                engine_type=engine_type,
                model_path=model_path,
                config=loader_config,
                creation_timeout=300.0,
                loading_timeout=300.0,
            )
            self.model_loaded = True

            if hasattr(self.engine, "set_crash_callback"):
                self.engine.set_crash_callback(self._on_engine_crash)

            logger.info("✅ [worker] Model engine loaded successfully")

        except (EngineTimeoutError, EngineInitializationError) as e:
            # Ensure model_loaded is False on any error
            self.model_loaded = False
            self.engine = None

            # Log detailed error context
            logger.error(f"❌ [worker] Model loading failed: {e}")
            logger.error(f"❌ [worker] Model path: {model_path}")
            logger.error(f"❌ [worker] Loader config: {loader_config}")

            # Check for common issues
            error_str = str(e).lower()
            if "cuda" in error_str or "gpu" in error_str:
                logger.error(
                    "❌ [worker] GPU/CUDA error - ensure CUDA is properly configured "
                    "and GPU has enough memory"
                )
            if "memory" in error_str or "oom" in error_str:
                logger.error(
                    "❌ [worker] Memory error - model may be too large for available "
                    "memory"
                )
            if "timeout" in error_str:
                logger.error(
                    "❌ [worker] Timeout error - model loading took too long, consider "
                    "increasing timeouts"
                )

            raise

        except Exception as e:
            # Handle unexpected errors
            self.model_loaded = False
            self.engine = None
            logger.error(f"❌ [worker] Unexpected error during model loading: {e}")
            raise

    async def process_command(self, command: dict[str, Any]) -> dict[str, Any]:
        """
        Process command by dispatching to appropriate handler.

        This implements the abstract method from WorkerProcess.
        Maps command_type to the appropriate handler method.

        Args:
            command: Command data containing command_type and other params

        Returns:
            Dict[str, Any]: Command result data

        Raises:
            ValueError: If command_type is missing or unknown
            Exception: Any processing errors from handlers
        """
        # Remove import - truncation now automatic

        command_type = command.get("command_type")

        if not command_type:
            logger.error(
                f"❌ [worker] process_command called with no command_type: {command}"
            )
            raise ValueError("Missing command_type in command")

        logger.info(f"🔧 [worker] Processing command: {command_type}")
        logger.debug(f"🔧 [worker] Full command data: {command}")

        # Map command types to handlers
        handlers_map = {
            "init_config": self.handle_init_config,
            "load_model": self.handle_load_model,
            "unload_model": self.handle_unload_model,
            "start_inference": self.handle_start_inference,
            "run_inference": self.handle_run_inference,
            "health": self.handle_health,
            "ping": self.handle_ping,  # Connectivity testing
            "cancel_inference": self.handle_cancel_inference,
            "count_tokens": self.handle_count_tokens,
            "debug_stats": self.handle_debug_stats,
            "get_model_info": self.handle_get_model_info,
            "list_models": self.handle_list_models,
        }

        handler = handlers_map.get(command_type)
        if not handler:
            logger.error(
                "❌ [worker] Unknown command type: %s, available: %s",
                command_type,
                list(handlers_map.keys()),
            )
            raise ValueError(f"Unknown command type: {command_type}")

        try:
            logger.info(
                "🔧 [worker] Calling handler %s for command %s",
                handler.__name__,
                command_type,
            )
            # Call the handler with command params
            # Note: handlers expect params dict, not the full command
            params = command.copy()
            # Remove command metadata that handlers don't need
            params.pop("command_type", None)
            params.pop("worker_id", None)
            params.pop("correlation_id", None)  # Don't pass correlation_id to engine

            result = await handler(params)

            # Ensure result is a dict
            if not isinstance(result, dict):
                logger.warning(
                    f"Handler {command_type} returned non-dict result: {type(result)}"
                )
                result = {"result": result}

            # Unicode + automatic truncation
            logger.info(
                "✅ [worker] Command %s completed successfully, result: %s",
                command_type,
                format_json_for_log(result),
            )
            return result

        except Exception as e:
            logger.error(
                f"❌ [worker] Command {command_type} failed: {e}", exc_info=True
            )
            raise
