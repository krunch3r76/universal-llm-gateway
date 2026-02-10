"""
VLLM engine quantization detection operations.

Handles detection and validation of AWQ and GPTQ quantization formats.
"""

import json
from universal_logging import get_logger
from pathlib import Path
from typing import Any

logger = get_logger(__name__)


class VLLMQuantizationDetector:
    """Handles quantization detection and validation for VLLM engine."""

    # Required configuration parameters that must be provided by client
    REQUIRED_PARAMS = {
        "max_model_len",  # Always required for vLLM
        "gpu_memory_utilization",  # Must be provided by client scripts
    }

    # Quantization-specific required parameters (in addition to base required params)
    AWQ_REQUIRED_PARAMS = set()  # No additional requirements for AWQ

    GPTQ_REQUIRED_PARAMS = {
        "dtype",  # GPTQ requires float16 - must be provided by client
    }

    def __init__(self, engine_instance: Any):
        """
        Initialize quantization detector with reference to engine instance.

        Args:
            engine_instance: The VLLMEngine instance to operate on
        """
        self.engine = engine_instance

    def detect_quantization(self) -> str | None:
        """Auto-detect quantization format from model files.

        Returns:
            "awq", "gptq", or None for unquantized models
        """
        config_path = Path(self.engine.model_path) / "config.json"

        if not config_path.exists():
            logger.warning(f"No config.json found at {config_path}")
            return None

        try:
            with open(config_path) as f:
                config = json.load(f)

            # Check for quantization_config field
            quant_config = config.get("quantization_config", {})

            if quant_config:
                quant_method = quant_config.get("quant_method", "").lower()

                if quant_method == "awq":
                    logger.info("Detected AWQ quantization from config")
                    return "awq"
                elif quant_method in ["gptq", "gptq-int4", "gptq-int8"]:
                    logger.info("Detected GPTQ quantization from config")
                    return "gptq"
                elif quant_method:
                    logger.warning(f"Unknown quantization method: {quant_method}")

            # Fallback: Check for model file patterns
            model_files = list(Path(self.engine.model_path).glob("*.safetensors"))
            model_files.extend(list(Path(self.engine.model_path).glob("*.bin")))

            for file in model_files:
                filename = file.name.lower()
                if "awq" in filename:
                    logger.info(f"Detected AWQ from filename: {filename}")
                    return "awq"
                elif "gptq" in filename or "exl2" in filename:
                    logger.info(f"Detected GPTQ from filename: {filename}")
                    return "gptq"

            return None

        except Exception as e:
            logger.warning(f"Error detecting quantization: {e}")
            return None

    def validate_required_params(
        self, params: dict[str, Any], quantization_format: str | None
    ) -> None:
        """Validate that all required parameters are provided.

        Args:
            params: Dictionary of parameters to validate
            quantization_format: "awq", "gptq", or None

        Raises:
            ValueError: If required parameters are missing
        """
        missing_params = []

        # Check base required parameters
        for param in self.REQUIRED_PARAMS:
            if param not in params:
                missing_params.append(param)

        # Check quantization-specific required parameters
        if quantization_format == "awq":
            for param in self.AWQ_REQUIRED_PARAMS:
                if param not in params:
                    missing_params.append(param)
        elif quantization_format == "gptq":
            for param in self.GPTQ_REQUIRED_PARAMS:
                if param not in params:
                    missing_params.append(param)

        if missing_params:
            all_required = list(self.REQUIRED_PARAMS)
            if quantization_format == "awq":
                all_required.extend(self.AWQ_REQUIRED_PARAMS)
            elif quantization_format == "gptq":
                all_required.extend(self.GPTQ_REQUIRED_PARAMS)

            raise ValueError(
                f"Missing required vLLM parameters: {', '.join(missing_params)}. "
                f"These must be provided by the client (e.g., vllm_model_config_generator.py). "
                f"All required parameters for {quantization_format or 'standard HF'} models: {', '.join(all_required)}"
            )
