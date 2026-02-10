"""
Engine factory for creating and loading model engines.

Provides unified interface for engine initialization with proper timeout handling.
"""

import asyncio
import inspect
from pathlib import Path
from typing import Any

from universal_logging import get_logger

logger = get_logger(__name__)


class EngineInitializationError(Exception):
    """Base exception for engine initialization failures."""

    def __init__(self, engine_type: str, error: Exception, context: dict[str, Any]):
        self.engine_type = engine_type
        self.original_error = error
        self.context = context
        super().__init__(f"{engine_type} initialization failed: {error}")


class EngineTimeoutError(EngineInitializationError):
    """Exception for engine initialization timeouts."""

    def __init__(self, engine_type: str, operation: str, timeout: float):
        context = {"operation": operation, "timeout": timeout}
        super().__init__(
            engine_type,
            TimeoutError(f"{operation} timed out after {timeout}s"),
            context,
        )


class EngineFactory:
    """
    Factory for creating and loading model engines with proper timeout handling.

    Provides unified interface for all engine types with consistent error handling
    and timeout behavior.
    """

    @staticmethod
    async def create_engine(
        engine_class: type,
        model_path: str,
        config: dict[str, Any],
        timeout: float = 300.0,
    ) -> Any:
        """
        Create an engine instance with timeout protection.

        Args:
            engine_class: Engine class to instantiate
            model_path: Path to the model file or directory
            config: Engine-specific configuration parameters
            timeout: Maximum time to wait for engine creation

        Returns:
            Initialized engine instance

        Raises:
            EngineTimeoutError: If creation exceeds timeout
            EngineInitializationError: If creation fails
        """
        engine_name = engine_class.__name__
        logger.info(f"🔧 [factory] Creating {engine_name} instance...")
        logger.info(f"🔧 [factory] Model path: {model_path}")
        logger.info(f"🔧 [factory] Config: {config}")
        logger.info(f"🔧 [factory] Timeout: {timeout}s")

        try:
            logger.info("🔧 [factory] Submitting engine creation to executor...")

            # Create a task that logs heartbeat while waiting
            async def create_with_heartbeat():
                loop = asyncio.get_running_loop()
                # run_in_executor returns a Future; do NOT wrap it in create_task
                task = loop.run_in_executor(
                    None, lambda: engine_class(model_path, **config)
                )

                # Log heartbeat every 10 seconds
                elapsed = 0
                while not task.done():
                    try:
                        await asyncio.wait_for(asyncio.shield(task), timeout=10.0)
                    except TimeoutError:
                        elapsed += 10
                        logger.info(
                            f"🔧 [factory] Still waiting for {engine_name} creation... "
                            f"({elapsed}s elapsed)"
                        )

                return await task

            engine = await asyncio.wait_for(create_with_heartbeat(), timeout=timeout)
            logger.info(f"✅ [factory] {engine_name} instance created successfully")
            return engine

        except TimeoutError as e:
            logger.error(
                f"❌ [factory] {engine_name} creation timed out after {timeout}s"
            )
            raise EngineTimeoutError(engine_name, "creation", timeout) from e

        except Exception as e:
            logger.error(f"❌ [factory] {engine_name} creation failed: {e}")
            context = {"model_path": model_path, "config": config}
            raise EngineInitializationError(engine_name, e, context) from e

    @staticmethod
    async def load_engine(
        engine: Any, engine_type: str, timeout: float = 300.0
    ) -> None:
        """
        Load an engine with timeout protection.

        Args:
            engine: Engine instance to load
            engine_type: Type of engine (for logging)
            timeout: Maximum time to wait for engine loading

        Raises:
            EngineTimeoutError: If loading exceeds timeout
            EngineInitializationError: If loading fails
        """
        logger.info(f"🔧 [factory] Loading {engine_type} engine...")

        try:
            # Support both coroutine-based and Future-returning load implementations
            if inspect.iscoroutinefunction(engine.load):
                await asyncio.wait_for(engine.load(), timeout=timeout)
            else:
                load_result = engine.load()
                if inspect.isawaitable(load_result):
                    await asyncio.wait_for(load_result, timeout=timeout)
                else:
                    # Synchronous load already completed
                    pass
            logger.info(f"✅ [factory] {engine_type} engine loaded successfully")

        except TimeoutError as e:
            logger.error(
                f"❌ [factory] {engine_type} engine loading timed out after {timeout}s"
            )
            raise EngineTimeoutError(engine_type, "loading", timeout) from e

        except Exception as e:
            logger.error(f"❌ [factory] {engine_type} engine loading failed: {e}")
            raise EngineInitializationError(engine_type, e, {}) from e

    @staticmethod
    def validate_model_path(model_path: str, engine_type: str) -> None:
        """
        Validate that model path exists and is accessible.

        For GGUF models: requires local file to exist.
        For HF/AWQ/GPTQ models: requires local directory to exist.
        For Whisper models: model identifier validated by faster-whisper.

        Args:
            model_path: Path to validate
            engine_type: Type of engine (for error reporting)

        Raises:
            FileNotFoundError: If model path doesn't exist
        """
        path = Path(model_path)

        # Whisper models use model identifiers (e.g., "large-v3", "medium")
        # faster-whisper handles auto-download from Hugging Face Hub
        if engine_type.lower() == "faster-whisper":
            # Model identifier format: tiny, base, small, medium, large-v2, large-v3
            logger.info(f"🔧 [factory] Whisper model identifier: {model_path}")
            return

        # Diffusers models use local directories (like HF/AWQ/GPTQ)
        # Models must be downloaded locally first - no auto-download from HF Hub
        if engine_type.lower() == "diffusers":
            # Flux/Flux.2 models are directory-based
            if not path.exists():
                raise FileNotFoundError(
                    f"Diffusers model directory not found: {model_path}"
                )
            if not path.is_dir():
                raise FileNotFoundError(
                    f"Diffusers model path must be a directory: {model_path}"
                )
            logger.info(f"🔧 [factory] Using local diffusers model: {model_path}")
            return

        # vLLM models (HF/AWQ/GPTQ) use directories
        if engine_type.lower() == "vllm":
            if not path.exists():
                raise FileNotFoundError(f"vLLM model directory not found: {model_path}")
            if not path.is_dir():
                raise FileNotFoundError(
                    f"vLLM model path must be a directory: {model_path}"
                )
            logger.info(f"🔧 [factory] Using local vLLM model: {model_path}")
            return

        # GGUF models require local file
        if engine_type.lower() == "native":
            if not path.exists():
                raise FileNotFoundError(f"GGUF model file not found: {model_path}")
            if path.is_file():
                file_size = path.stat().st_size / (1024**3)
                logger.info(f"🔧 [factory] GGUF model file size: {file_size:.2f} GB")
            return

    @staticmethod
    def get_engine_class(engine_type: str):
        """
        Get engine class for the specified type.

        Args:
            engine_type: Engine type identifier
                (native, vllm, faster-whisper, diffusers)

        Returns:
            Engine class

        Raises:
            ImportError: If engine module is not available
            ValueError: If engine type is not supported
        """
        engine_type = engine_type.lower()

        if engine_type == "native":
            # NativeGGUFEngine: llama-server binary with parallel batching
            try:
                from inference_djinn.engines.gguf.native import NativeGGUFEngine

                return NativeGGUFEngine
            except ImportError as e:
                raise ImportError(
                    "NativeGGUFEngine not available - "
                    "cannot import native llama-server integration"
                ) from e

        elif engine_type == "vllm":
            try:
                from inference_djinn.engines.vllm.engine import VLLMEngine

                return VLLMEngine
            except ImportError as e:
                raise ImportError(
                    "vLLM engine is not available - inference_djinn module not found"
                ) from e

        elif engine_type == "faster-whisper":
            try:
                from inference_djinn.engines.whisper.engine.engine import WhisperEngine

                return WhisperEngine
            except ImportError as e:
                raise ImportError(
                    "Whisper engine not available - faster-whisper not installed. "
                    "Install with: pip install faster-whisper soundfile scipy"
                ) from e

        elif engine_type == "diffusers":
            try:
                from inference_djinn.engines.flux.engine.engine import FluxEngine

                return FluxEngine
            except ImportError as e:
                raise ImportError(
                    "Diffusers engine not available - diffusers not installed. "
                    "Install with: pip install diffusers>=0.36.0 "
                    "transformers accelerate"
                ) from e

        else:
            raise ValueError(f"Unsupported engine type: {engine_type}")

    @classmethod
    async def create_and_load(
        cls,
        engine_type: str,
        model_path: str,
        config: dict[str, Any],
        creation_timeout: float = 300.0,
        loading_timeout: float = 300.0,
    ) -> Any:
        """
        Create and load an engine in one operation.

        Args:
            engine_type: Type of engine to create
            model_path: Path to the model
            config: Engine configuration
            creation_timeout: Timeout for engine creation
            loading_timeout: Timeout for engine loading

        Returns:
            Loaded engine instance

        Raises:
            EngineTimeoutError: If any step exceeds timeout
            EngineInitializationError: If any step fails
            FileNotFoundError: If model path doesn't exist
            ImportError: If engine module is not available
            ValueError: If engine type is not supported
        """
        # Validate model path
        cls.validate_model_path(model_path, engine_type)

        # Get engine class
        engine_class = cls.get_engine_class(engine_type)

        # Create engine instance
        engine = await cls.create_engine(
            engine_class, model_path, config, timeout=creation_timeout
        )

        # Load engine
        await cls.load_engine(engine, engine_type, timeout=loading_timeout)

        return engine
