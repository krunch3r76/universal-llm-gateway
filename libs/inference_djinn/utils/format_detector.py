"""
Model format detection utility for inference_djinn.

Detects model quantization formats (AWQ, GPTQ, EXL2, EXL3) and standard HuggingFace models
to automatically select the appropriate engine.

NOTE: VLLMEngine auto-detects quantization formats, so this module is optional for most use cases.
It's primarily useful for scripts that need to determine the engine type before instantiation
(e.g., model_format_detector.py utility script).
"""

import json
from pathlib import Path
from typing import Any


class ModelFormatDetector:
    """Utility class to detect model format and recommend appropriate engine"""

    @staticmethod
    def detect_format(model_path: str) -> str:
        """
        Detect the format of a model directory.

        Returns:
            - "exl3" for EXL3 models
            - "awq" for AWQ quantized models
            - "gptq" for GPTQ/EXL2 models
            - "gguf" for GGUF quantized models
            - "hf" for standard HuggingFace models
            - "unknown" if format cannot be determined
        """
        model_path = Path(model_path)

        if not model_path.exists():
            return "unknown"

        # Check for GGUF indicators first (single file)
        if ModelFormatDetector._is_gguf_model(model_path):
            return "gguf"

        # Check for EXL3 indicators
        if ModelFormatDetector._is_exl3_model(model_path):
            return "exl3"

        # Check for AWQ indicators
        if ModelFormatDetector._is_awq_model(model_path):
            return "awq"

        # Check for GPTQ/EXL2 indicators
        if ModelFormatDetector._is_gptq_model(model_path):
            return "gptq"

        # Check if it's a standard HuggingFace model
        if ModelFormatDetector._is_hf_model(model_path):
            return "hf"

        return "unknown"

    @staticmethod
    def _is_gguf_model(model_path: Path) -> bool:
        """Check if model is GGUF format"""

        # GGUF models are typically single files with .gguf extension
        if model_path.is_file() and model_path.suffix.lower() in [".gguf", ".ggml"]:
            return True

        # Check if it's a directory containing GGUF files
        if model_path.is_dir():
            gguf_files = list(model_path.glob("*.gguf")) + list(
                model_path.glob("*.ggml")
            )
            if gguf_files:
                return True

        return False

    @staticmethod
    def _is_awq_model(model_path: Path) -> bool:
        """Check if model is AWQ format"""

        # Check config.json for AWQ indicators
        config_path = model_path / "config.json"
        if config_path.exists():
            try:
                with open(config_path) as f:
                    config = json.load(f)

                # Look for quantization config indicating AWQ
                quant_config = config.get("quantization_config", {})
                if quant_config.get("quant_method", "").lower() == "awq":
                    return True
                if quant_config.get("version") == "awq":
                    return True

            except (json.JSONDecodeError, OSError):
                pass

        # Check for AWQ-specific file patterns
        safetensors_files = list(model_path.glob("*.safetensors"))
        for file in safetensors_files:
            filename = file.name.lower()
            if "awq" in filename:
                return True

        # Check for AWQ quantization config file
        awq_config_path = model_path / "quant_config.json"
        if awq_config_path.exists():
            try:
                with open(awq_config_path) as f:
                    config = json.load(f)
                    if config.get("quant_method", "").lower() == "awq":
                        return True
                    if "w_bit" in config and "q_group_size" in config:
                        # AWQ-specific config parameters
                        return True
            except (json.JSONDecodeError, OSError):
                pass

        return False

    @staticmethod
    def _is_hf_model(model_path: Path) -> bool:
        """Check if model is a standard HuggingFace model"""

        # Must have config.json
        config_path = model_path / "config.json"
        if not config_path.exists():
            return False

        # Check for model weight files
        has_safetensors = len(list(model_path.glob("*.safetensors"))) > 0
        has_pytorch = len(list(model_path.glob("*.bin"))) > 0
        has_pytorch_pt = len(list(model_path.glob("*.pt"))) > 0
        has_pytorch_pth = len(list(model_path.glob("*.pth"))) > 0

        # Must have weight files but not be a quantized format
        if has_safetensors or has_pytorch or has_pytorch_pt or has_pytorch_pth:
            # Already checked for quantized formats, so this is standard HF
            return True

        return False

    @staticmethod
    def _is_exl3_model(model_path: Path) -> bool:
        """Check if model is EXL3 format"""

        # Check for .exl3 files
        exl3_files = list(model_path.glob("*.exl3"))
        if exl3_files:
            return True

        # Check for quantization config indicating EXL3
        quant_config_path = model_path / "quant_config.json"
        if quant_config_path.exists():
            try:
                with open(quant_config_path) as f:
                    config = json.load(f)

                # Look for EXL3-specific indicators
                if config.get("quant_method") == "exl3":
                    return True
                if config.get("version", 0) >= 3:
                    return True
                if "exl3" in str(config).lower():
                    return True

            except (json.JSONDecodeError, OSError):
                pass

        # Check config.json for EXL3 indicators
        config_path = model_path / "config.json"
        if config_path.exists():
            try:
                with open(config_path) as f:
                    config = json.load(f)

                # Look for quantization config indicating EXL3
                quant_config = config.get("quantization_config", {})
                if quant_config.get("quant_method") == "exl3":
                    return True
                if quant_config.get("version", 0) >= 3:
                    return True

            except (json.JSONDecodeError, OSError):
                pass

        # Check for EXL3-specific file patterns
        safetensors_files = list(model_path.glob("*.safetensors"))
        for file in safetensors_files:
            if "exl3" in file.name.lower():
                return True

        return False

    @staticmethod
    def _is_gptq_model(model_path: Path) -> bool:
        """Check if model is GPTQ/EXL2 format"""

        # Check for .safetensors files (common in GPTQ)
        safetensors_files = list(model_path.glob("*.safetensors"))
        if not safetensors_files:
            return False

        # Check for quantization config indicating GPTQ
        quant_config_path = model_path / "quantize_config.json"
        if quant_config_path.exists():
            try:
                with open(quant_config_path) as f:
                    config = json.load(f)

                # Look for GPTQ-specific indicators
                if config.get("quant_method") == "gptq":
                    return True
                if config.get("bits") in [2, 3, 4, 8]:  # Common GPTQ bit widths
                    return True

            except (json.JSONDecodeError, OSError):
                pass

        # Check config.json for GPTQ indicators
        config_path = model_path / "config.json"
        if config_path.exists():
            try:
                with open(config_path) as f:
                    config = json.load(f)

                # Look for quantization config indicating GPTQ
                quant_config = config.get("quantization_config", {})
                if quant_config.get("quant_method") == "gptq":
                    return True
                if quant_config.get("bits") in [2, 3, 4, 8]:
                    return True

            except (json.JSONDecodeError, OSError):
                pass

        # Check for GPTQ-specific file patterns
        for file in safetensors_files:
            filename = file.name.lower()
            if any(
                pattern in filename for pattern in ["gptq", "4bit", "8bit", "q4", "q8"]
            ):
                return True

        # Check for model.safetensors.index.json (common in GPTQ)
        index_file = model_path / "model.safetensors.index.json"
        if index_file.exists():
            return True

        # If we have safetensors files but no clear EXL3 indicators, assume GPTQ
        return True

    @staticmethod
    def get_recommended_engine(model_path: str) -> str:
        """
        Get the recommended engine for a model based on its format.

        Args:
            model_path: Path to the model directory

        Returns:
            - "vllm" for AWQ, GPTQ, and HF models
            - "gguf" for GGUF models
            - "exllamav3" for EXL3 models
            - "vllm" for unknown formats (fallback)
        """
        format_type = ModelFormatDetector.detect_format(model_path)

        if format_type == "exl3":
            # EXL3 requires dedicated ExLlamaV3 engine
            return "exllamav3"
        elif format_type == "gguf":
            # GGUF requires dedicated GGUF engine
            return "gguf"
        elif format_type in ["awq", "gptq", "hf"]:
            # Use unified vLLM engine for AWQ, GPTQ, and HF models
            return "vllm"
        else:
            # Unknown format, default to vLLM
            return "vllm"

    @staticmethod
    def get_format_info(model_path: str) -> dict[str, Any]:
        """Get detailed format information about a model"""
        model_path = Path(model_path)

        info = {
            "model_path": str(model_path),
            "format": ModelFormatDetector.detect_format(str(model_path)),
            "recommended_engine": ModelFormatDetector.get_recommended_engine(
                str(model_path)
            ),
            "files": {
                "safetensors": list(model_path.glob("*.safetensors"))
                if model_path.exists()
                else [],
                "exl3": list(model_path.glob("*.exl3")) if model_path.exists() else [],
                "config": model_path / "config.json" if model_path.exists() else None,
                "quant_config": model_path / "quantize_config.json"
                if model_path.exists()
                else None,
            },
        }

        # Convert Path objects to strings for JSON serialization
        for key, value in info["files"].items():
            if isinstance(value, list):
                info["files"][key] = [str(p) for p in value]
            elif value and hasattr(value, "exists"):
                info["files"][key] = str(value) if value.exists() else None

        return info


def detect_model_format(model_path: str) -> str:
    """Convenience function for model format detection"""
    return ModelFormatDetector.detect_format(model_path)


def get_recommended_engine(model_path: str) -> str:
    """Convenience function for engine recommendation"""
    return ModelFormatDetector.get_recommended_engine(model_path)
