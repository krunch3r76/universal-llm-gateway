"""
Model discovery and metadata extraction using inference_djinn inspectors.

Automatically generates complete, schema-compliant model configurations
from model files by leveraging inference_djinn's inspection capabilities.
"""

from pathlib import Path
from typing import Any, Literal

from universal_logging import get_logger

logger = get_logger(__name__)


class DiscoveryError(Exception):
    """Raised when model discovery fails"""

    pass


class ModelDiscovery:
    """
    Discover model metadata from files using inference_djinn inspectors.

    Maps djinn inspector output to complete Pydantic schema-compliant
    configurations ready for adding to model_loaders.yaml.
    """

    def __init__(self):
        """Initialize model discovery"""
        self.djinn_available = self._check_djinn_availability()

    def _check_djinn_availability(self) -> bool:
        """Check if inference_djinn is available"""
        import importlib.util

        if importlib.util.find_spec("inference_djinn") is not None:
            return True
        else:
            logger.warning(
                "inference_djinn not available - discovery features disabled"
            )
            return False

    def discover_gguf(self, model_path: str | Path) -> dict[str, Any]:
        """
        Discover GGUF model metadata.

        Args:
            model_path: Path to .gguf model file

        Returns:
            Complete GGUF model configuration dictionary

        Raises:
            DiscoveryError: If discovery fails
        """
        if not self.djinn_available:
            raise DiscoveryError("inference_djinn not available")

        model_path = Path(model_path)

        if not model_path.exists():
            raise DiscoveryError(f"Model file not found: {model_path}")

        if not model_path.suffix.lower() == ".gguf":
            raise DiscoveryError(f"Not a GGUF file: {model_path}")

        try:
            from inference_djinn.engines.gguf.inspector import get_model_info_summary

            # Call djinn inspector
            djinn_info = get_model_info_summary(str(model_path))

            # Map to schema-compliant config
            config = self._map_gguf_to_config(djinn_info, model_path)

            return config

        except ImportError as e:
            raise DiscoveryError(f"Failed to import GGUF inspector: {e}")
        except Exception as e:
            raise DiscoveryError(f"Failed to discover GGUF model: {e}")

    def discover_hf(
        self, model_path: str | Path, format_hint: Literal["hf", "gptq", "awq"] = "hf"
    ) -> dict[str, Any]:
        """
        Discover HuggingFace/vLLM model metadata.

        Args:
            model_path: Path to model directory
            format_hint: Format hint ('hf', 'gptq', 'awq')

        Returns:
            Complete HF model configuration dictionary

        Raises:
            DiscoveryError: If discovery fails
        """
        if not self.djinn_available:
            raise DiscoveryError("inference_djinn not available")

        model_path = Path(model_path)

        if not model_path.exists():
            raise DiscoveryError(f"Model directory not found: {model_path}")

        if not model_path.is_dir():
            raise DiscoveryError(f"Not a directory: {model_path}")

        try:
            # All HF-based formats (awq, gptq, hf) use vLLM inspector
            from inference_djinn.engines.vllm.inspector import (
                get_vllm_model_info as get_model_info_summary,
            )

            # Call djinn inspector
            djinn_info = get_model_info_summary(str(model_path))

            # Map to schema-compliant config
            config = self._map_hf_to_config(djinn_info, model_path, format_hint)

            return config

        except ImportError as e:
            raise DiscoveryError(
                f"Failed to import {format_hint.upper()} inspector: {e}"
            )
        except Exception as e:
            raise DiscoveryError(f"Failed to discover {format_hint.upper()} model: {e}")

    def _map_gguf_to_config(
        self, djinn_info: dict[str, Any], model_path: Path
    ) -> dict[str, Any]:
        """
        Map djinn GGUF inspector output to schema-compliant config.

        Args:
            djinn_info: Output from djinn inspector
            model_path: Path to model file

        Returns:
            Complete GGUF configuration dictionary
        """
        # Extract metadata from djinn output
        metadata = djinn_info.get("metadata", {})
        architecture = djinn_info.get("architecture", {})

        # Infer model name from filename if not in metadata
        model_name = metadata.get("name") or model_path.stem

        # Infer model_id from filename (safe format)
        model_id = model_name.lower().replace(" ", "-").replace("_", "-")

        # Extract architecture info
        family = architecture.get("family", "llama")
        arch = architecture.get("architecture", "llama")

        # Extract quantization
        # GGUF file_type is numeric (7 = Q8_0), convert to bits
        file_type = metadata.get("file_type")
        quant = None

        if file_type is not None:
            # Map GGUF file_type to quantization bits
            # Source: https://github.com/ggerganov/ggml/blob/master/docs/gguf.md
            file_type_map = {
                0: 32,  # F32
                1: 16,  # F16
                2: 4,  # Q4_0
                3: 4,  # Q4_1
                7: 8,  # Q8_0
                8: 5,  # Q5_0
                9: 5,  # Q5_1
                10: 2,  # Q2_K
                11: 3,  # Q3_K_S
                12: 3,  # Q3_K_M
                13: 3,  # Q3_K_L
                14: 4,  # Q4_K_S
                15: 4,  # Q4_K_M
                16: 5,  # Q5_K_S
                17: 5,  # Q5_K_M
                18: 6,  # Q6_K
                19: 1,  # IQ2_XXS
                20: 2,  # IQ2_XS
                21: 3,  # IQ3_XXS
                22: 3,  # IQ3_S
                23: 1,  # IQ1_S
                24: 4,  # IQ4_NL
                25: 4,  # IQ4_XS
                26: 2,  # IQ2_S
                27: 1,  # IQ1_M
                28: 16,  # BF16
            }
            quant = file_type_map.get(file_type)

        # Fallback to filename-based detection
        if quant is None:
            quant = self._infer_quant_from_filename(model_path.name)

        # Extract context length
        training_ctx = metadata.get("context_length")
        supports_chat = metadata.get("supports_chat", True)
        input_schema = "messages" if supports_chat else "prompt"

        capabilities: dict[str, Any] = metadata.get("capabilities") or {}
        if not capabilities:
            capabilities = {
                "input_schema": input_schema,
                "modalities": {"input": ["text"], "output": ["text"]},
                "interaction": {"chat_template": supports_chat},
                "reasoning": {"supports_thinking": False},
                "limits": {"max_context_length": training_ctx} if training_ctx else {},
                "provenance": {"license": metadata.get("license")}
                if metadata.get("license")
                else {},
            }

        # Build complete GGUF config
        config = {
            "info": {
                "name": model_name,
                "format": "gguf",
                "path": str(model_path.absolute()),
                "enabled": True,
                "family": family,
                "arch": arch,
                "quant": quant,
                "parameters": metadata.get("parameters"),
                "training_cutoff_year": metadata.get("training_cutoff_year"),
                "training_context_length": training_ctx,
                "release_date": metadata.get("release_date"),
                "description": metadata.get("description"),
                "capabilities": capabilities,
                "openai_api_fields": {
                    "id": model_id,
                    "object": "model",
                    "owned_by": "universal-llm-gateway",
                    "permission": ["generate"],
                },
            },
            "base_loader": {
                "n_batch": 512,
                "f16_kv": True,
                "use_mmap": True,
                "use_mlock": True,
                "verbose": False,
            },
            "profiles": {
                str(training_ctx or 32768): {
                    "loader": {"n_ctx": training_ctx or 32768, "n_gpu_layers": -1},
                    "resources": {"ram_mb": None, "vram_mb": None},
                    "default": True,
                }
            },
        }

        return config

    def _map_hf_to_config(
        self, djinn_info: dict[str, Any], model_path: Path, format_hint: str
    ) -> dict[str, Any]:
        """
        Map djinn HF/GPTQ/AWQ inspector output to schema-compliant config.

        Args:
            djinn_info: Output from djinn inspector
            model_path: Path to model directory
            format_hint: Format hint ('hf', 'gptq', 'awq')

        Returns:
            Complete HF configuration dictionary
        """
        # Extract metadata from djinn output
        metadata = djinn_info.get("metadata", {})
        architecture = djinn_info.get("architecture", {})

        # Infer model name from directory name if not in metadata
        model_name = metadata.get("name") or model_path.name

        # Infer model_id from directory name
        model_id = model_name.lower().replace(" ", "-").replace("_", "-")

        # Extract architecture info
        family = architecture.get("family", "unknown")
        arch = architecture.get("architecture", "unknown")

        # Extract quantization for GPTQ/AWQ
        quant = None
        if format_hint == "gptq":
            quant = metadata.get("quantization", "gptq-4bit")
        elif format_hint == "awq":
            quant = metadata.get("quantization", "awq-4bit")

        # Extract context length
        training_ctx = metadata.get("context_length")
        max_model_len = metadata.get("max_model_len", training_ctx or 8192)
        supports_chat = metadata.get("supports_chat", True)
        input_schema = "messages" if supports_chat else "prompt"

        capabilities: dict[str, Any] = metadata.get("capabilities") or {}
        if not capabilities:
            capabilities = {
                "input_schema": input_schema,
                "modalities": {"input": ["text"], "output": ["text"]},
                "interaction": {"chat_template": supports_chat},
                "reasoning": {"supports_thinking": False},
                "limits": {"max_context_length": training_ctx} if training_ctx else {},
                "provenance": {"license": metadata.get("license")}
                if metadata.get("license")
                else {},
            }

        # Build complete HF config
        config = {
            "info": {
                "name": model_name,
                "format": format_hint,  # Keep original format (gptq/awq will be normalized on validation)
                "path": str(model_path.absolute()),
                "enabled": True,
                "family": family,
                "arch": arch,
                "quant": quant,
                "parameters": metadata.get("parameters"),
                "training_cutoff_year": metadata.get("training_cutoff_year"),
                "training_context_length": training_ctx,
                "release_date": metadata.get("release_date"),
                "description": metadata.get("description"),
                "capabilities": capabilities,
                "openai_api_fields": {
                    "id": model_id,
                    "object": "model",
                    "owned_by": "universal-llm-gateway",
                    "permission": ["generate"],
                },
            },
            "base_loader": {
                "trust_remote_code": False,  # SECURITY: Never trust remote code
                "max_model_len": max_model_len,  # From model inspection
                "disable_custom_all_reduce": True,  # Stability
                "disable_log_stats": True,  # Reduce noise
            },
            "profiles": {
                str(max_model_len): {
                    "loader": {"max_model_len": max_model_len},
                    "resources": {"ram_mb": None, "vram_mb": None},
                    "default": True,
                }
            },
        }

        return config

    def _infer_quant_from_filename(self, filename: str) -> int | None:
        """
        Infer quantization bits from filename.

        Args:
            filename: Model filename

        Returns:
            Quantization bits (integer) or None
        """
        filename_upper = filename.upper()

        # Common GGUF quantization patterns mapped to bits
        # Order matters: check more specific patterns first
        quant_patterns = {
            "Q2_K": 2,
            "Q3_K": 3,
            "Q4_K": 4,
            "Q4_0": 4,
            "Q4_1": 4,
            "Q5_K": 5,
            "Q5_0": 5,
            "Q5_1": 5,
            "Q6_K": 6,
            "Q8_K": 8,  # Q8_K_XL, Q8_K_M, etc.
            "Q8_0": 8,
            "IQ1": 1,
            "IQ2": 2,
            "IQ3": 3,
            "IQ4": 4,
            "F16": 16,
            "BF16": 16,
            "F32": 32,
        }

        for pattern, bits in quant_patterns.items():
            if pattern in filename_upper:
                return bits

        return None

    def discover_auto(self, model_path: str | Path) -> dict[str, Any]:
        """
        Auto-detect model type and discover metadata.

        Args:
            model_path: Path to model file or directory

        Returns:
            Complete model configuration dictionary

        Raises:
            DiscoveryError: If discovery fails or format cannot be detected
        """
        model_path = Path(model_path)

        if not model_path.exists():
            raise DiscoveryError(f"Path not found: {model_path}")

        # Detect format
        if model_path.is_file() and model_path.suffix.lower() == ".gguf":
            return self.discover_gguf(model_path)

        elif model_path.is_dir():
            # Check for GPTQ/AWQ markers in directory
            config_file = model_path / "config.json"
            if config_file.exists():
                try:
                    import json

                    with open(config_file) as f:
                        config_data = json.load(f)

                    quant_config = config_data.get("quantization_config", {})
                    quant_method = quant_config.get("quant_method", "").lower()

                    if "gptq" in quant_method:
                        return self.discover_hf(model_path, format_hint="gptq")
                    elif "awq" in quant_method:
                        return self.discover_hf(model_path, format_hint="awq")
                except Exception:
                    pass

            # Default to HF
            return self.discover_hf(model_path, format_hint="hf")

        else:
            raise DiscoveryError(f"Unsupported model path type: {model_path}")


def discover_model(model_path: str | Path) -> dict[str, Any]:
    """
    Convenience function to discover model configuration.

    Args:
        model_path: Path to model file or directory

    Returns:
        Complete model configuration dictionary
    """
    discovery = ModelDiscovery()
    return discovery.discover_auto(model_path)
