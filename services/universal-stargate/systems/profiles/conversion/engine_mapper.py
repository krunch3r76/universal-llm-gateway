"""
Engine format mapping - maps model formats to inference engines.

Determines which inference engine (llama-cpp, vLLM, etc.) a model uses
based on its format field.

Note: Uses hardcoded defaults since engine mappings are stable and don't
require external configuration. This avoids another required config file.
"""

from universal_logging import get_logger

logger = get_logger(__name__)


class EngineMapper:
    """Maps model formats to inference engines for parameter conversion."""

    # Hardcoded mappings - these are stable and don't need external config
    DEFAULT_MAPPINGS = {
        "gguf": "llama_cpp",
        "awq": "vllm",
        "gptq": "vllm",
        "hf": "vllm",
        "huggingface": "vllm",
        "safetensors": "vllm",
    }

    DEFAULT_ENGINE = "vllm"

    def __init__(self) -> None:
        """Initialize the engine mapper with default mappings."""
        self._format_to_engine = self.DEFAULT_MAPPINGS.copy()
        count = len(self._format_to_engine)
        logger.debug(f"EngineMapper initialized with {count} format mappings")

    def get_engine(self, model_format: str | None) -> str:
        """
        Get the engine name for a given model format.

        Args:
            model_format: The model format (e.g., 'gguf', 'awq', 'gptq')

        Returns:
            Engine name (e.g., 'llama_cpp', 'vllm')
        """
        if not model_format:
            return self.DEFAULT_ENGINE

        fmt_lower = model_format.lower()
        engine = self._format_to_engine.get(fmt_lower)

        if engine is None:
            logger.warning(
                f"Unknown format '{model_format}', defaulting to: {self.DEFAULT_ENGINE}"
            )
            return self.DEFAULT_ENGINE

        return engine

    def is_llama_cpp(self, model_format: str | None) -> bool:
        """Check if the format uses llama.cpp engine."""
        return self.get_engine(model_format) == "llama_cpp"

    def is_vllm(self, model_format: str | None) -> bool:
        """Check if the format uses vLLM engine."""
        return self.get_engine(model_format) == "vllm"
