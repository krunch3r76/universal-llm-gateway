#!/usr/bin/env python3
"""
vLLM Model Analyzer

A comprehensive command-line tool for analyzing vLLM-compatible model directories.
Supports HuggingFace, GPTQ, and AWQ models.
Provides detailed information about model capabilities, chat templates,
configuration recommendations, and gateway-compatible YAML generation with profiles.

Usage:
    python scripts/vllm_model_config_generator.py [model_path] [options]

Examples:
    python scripts/vllm_model_config_generator.py /path/to/model
    python scripts/vllm_model_config_generator.py /path/to/model --json
    python scripts/vllm_model_config_generator.py /path/to/model --verbose
    python scripts/vllm_model_config_generator.py /path/to/model --yaml --contexts 8192,16384
"""

import argparse
import hashlib
import json
from universal_logging import get_logger, INFO
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

# Configure logging (universal_logging is already imported)
logger = get_logger(__name__)

# Add the project root to the path so we can import from engines
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

# Try to import Hugging Face dependencies
try:
    from transformers import AutoConfig, AutoModelForCausalLM, AutoTokenizer
    from transformers import __version__ as transformers_version

    transformers_available = True
except ImportError:
    transformers_available = False
    logger.warning(
        "Transformers not available - some analysis features will be limited"
    )

try:
    import torch

    torch_available = True
except ImportError:
    torch_available = False
    logger.warning("PyTorch not available - some analysis features will be limited")


@dataclass
class HFMetadataLite:
    """Simplified Hugging Face metadata container."""

    name: str = "unknown"
    architecture: str = "unknown"
    context_length: int = 0
    hidden_size: int = 0
    num_layers: int = 0
    num_attention_heads: int = 0
    num_key_value_heads: int = 0
    vocab_size: int = 0
    model_type: str = ""
    chat_template: str | None = None
    tokenizer_class: str = ""
    torch_dtype: str = ""
    transformers_version: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "name": self.name,
            "architecture": self.architecture,
            "context_length": self.context_length,
            "hidden_size": self.hidden_size,
            "num_layers": self.num_layers,
            "num_attention_heads": self.num_attention_heads,
            "num_key_value_heads": self.num_key_value_heads,
            "vocab_size": self.vocab_size,
            "model_type": self.model_type,
            "chat_template": self.chat_template,
            "tokenizer_class": self.tokenizer_class,
            "torch_dtype": self.torch_dtype,
            "transformers_version": self.transformers_version,
        }


def load_hf_metadata(model_path: str) -> HFMetadataLite | None:
    """Load basic Hugging Face metadata from model directory."""
    if not os.path.isdir(model_path):
        logger.debug(f"Model directory not found: {model_path}")
        return None

    if not transformers_available:
        logger.debug("Transformers not available")
        return None

    try:
        # Load config
        config = AutoConfig.from_pretrained(model_path, trust_remote_code=True)

        # Load tokenizer
        try:
            tokenizer = AutoTokenizer.from_pretrained(
                model_path, trust_remote_code=True
            )
        except Exception as e:
            logger.debug(f"Could not load tokenizer: {e}")
            tokenizer = None

        meta = HFMetadataLite()

        # Extract basic metadata from config
        meta.architecture = (
            getattr(config, "architectures", ["unknown"])[0]
            if hasattr(config, "architectures")
            else "unknown"
        )
        meta.model_type = getattr(config, "model_type", "unknown")
        meta.hidden_size = getattr(config, "hidden_size", 0)
        meta.num_layers = getattr(config, "num_hidden_layers", 0)
        meta.num_attention_heads = getattr(config, "num_attention_heads", 0)
        meta.num_key_value_heads = getattr(config, "num_key_value_heads", 0)
        meta.vocab_size = getattr(config, "vocab_size", 0)
        meta.torch_dtype = str(getattr(config, "torch_dtype", "unknown"))
        meta.transformers_version = transformers_version

        # Get context length from various possible fields
        context_length = 0
        for field in [
            "max_position_embeddings",
            "n_positions",
            "max_sequence_length",
            "max_length",
        ]:
            if hasattr(config, field):
                value = getattr(config, field)
                if isinstance(value, (int, float)) and value > context_length:
                    context_length = int(value)

        meta.context_length = context_length

        # Get chat template from tokenizer
        if (
            tokenizer
            and hasattr(tokenizer, "chat_template")
            and tokenizer.chat_template
        ):
            meta.chat_template = tokenizer.chat_template

        # Get tokenizer class
        if tokenizer:
            meta.tokenizer_class = tokenizer.__class__.__name__

        # Extract model name from path
        meta.name = os.path.basename(model_path)

        return meta

    except Exception as e:
        logger.error(f"Could not load HF metadata from {model_path}: {e}")
        import traceback

        logger.error(traceback.format_exc())
        return None


def detect_model_type_from_metadata(meta: HFMetadataLite) -> str:
    """Heuristically determine model type from HF metadata."""
    arch = meta.architecture.lower()
    name = meta.name.lower()
    model_type = meta.model_type.lower()

    # Debug output
    logger.debug(
        f"Model type detection - arch: {arch}, name: {name}, model_type: {model_type}"
    )

    # Check name field for specific model types first
    patterns = {
        "qwen": ["qwen"],
        "llama": ["llama"],
        "mistral": ["mistral"],
        "mixtral": ["mixtral"],
        "codellama": ["codellama"],
        "deepseek": ["deepseek"],
        "phi": ["phi"],
        "gemma": ["gemma"],
        "falcon": ["falcon"],
        "bloom": ["bloom"],
        "gpt": ["gpt"],
        "t5": ["t5"],
        "bert": ["bert"],
    }

    for model_type_name, keywords in patterns.items():
        if any(keyword in name for keyword in keywords) or any(
            keyword in arch for keyword in keywords
        ):
            logger.debug(f"Detected model type: {model_type_name}")
            return model_type_name

    # Check model_type field as fallback
    if model_type in [
        "llama",
        "mistral",
        "mixtral",
        "qwen",
        "codellama",
        "deepseek",
        "phi",
        "gemma",
        "falcon",
        "bloom",
        "gpt",
        "t5",
        "bert",
    ]:
        logger.debug(f"Detected model type from model_type: {model_type}")
        return model_type

    logger.debug("No specific model type detected, returning unknown")
    return "unknown"


def detect_quantization_format(model_path: str) -> tuple[str, int | None]:
    """
    Detect quantization format (awq, gptq, or hf for standard HF).

    Returns:
        Tuple of (format, bits) where:
        - format: "awq", "gptq", or "hf"
        - bits: quantization bits (e.g., 4 for 4-bit) or None
    """
    config_path = os.path.join(model_path, "config.json")

    if not os.path.exists(config_path):
        return "hf", None

    try:
        with open(config_path) as f:
            config = json.load(f)

        # Check for quantization_config
        quant_config = config.get("quantization_config", {})

        if quant_config:
            quant_method = quant_config.get("quant_method", "").lower()

            # Detect AWQ
            if quant_method == "awq" or quant_config.get("version") == "awq":
                bits = quant_config.get("bits", quant_config.get("w_bit", 4))
                return "awq", bits

            # Detect GPTQ
            if quant_method == "gptq":
                bits = quant_config.get("bits", 4)
                return "gptq", bits

        return "hf", None

    except (json.JSONDecodeError, OSError) as e:
        logger.debug(f"Error reading config.json: {e}")
        return "hf", None


def get_max_model_len_from_config(model_path: str) -> int | None:
    """
    Extract max_model_len from model's config.json.
    Checks: max_position_embeddings, n_positions, max_sequence_length, max_length.
    Returns the actual value from the model, never a default.
    """
    config_path = os.path.join(model_path, "config.json")

    if not os.path.exists(config_path):
        return None

    try:
        with open(config_path) as f:
            config = json.load(f)

        # Check fields in priority order
        for field in [
            "max_position_embeddings",
            "n_positions",
            "max_sequence_length",
            "max_length",
        ]:
            if field in config:
                value = config[field]
                if isinstance(value, (int, float)) and value > 0:
                    return int(value)

        return None

    except (json.JSONDecodeError, OSError) as e:
        logger.debug(f"Error reading config.json: {e}")
        return None


def detect_rope_scaling(model_path: str) -> bool:
    """
    Check if the model uses RoPE scaling.

    Returns:
        True if model uses RoPE scaling, False otherwise
    """
    config_path = os.path.join(model_path, "config.json")

    if not os.path.exists(config_path):
        return False

    try:
        with open(config_path) as f:
            config = json.load(f)

        # Check for RoPE scaling
        rope_scaling = config.get("rope_scaling")
        if rope_scaling and isinstance(rope_scaling, dict):
            # RoPE scaling is present if it's a non-empty dict
            return True

        return False

    except (OSError, json.JSONDecodeError):
        return False


def detect_chat_template_support(
    model_path: str, meta: HFMetadataLite | None = None
) -> dict[str, Any]:
    """Analyze HF model for chat template support."""
    if meta is None:
        meta = load_hf_metadata(model_path)

    model_name = os.path.basename(model_path).lower()
    analysis = {
        "model_path": model_path,
        "model_name": model_name,
        "has_chat_template": False,
        "template_type": "none",
        "tokenizer_available": False,
        "recommendations": {},
    }

    if meta is None:
        analysis.update(
            {
                "template_type": "simple_formatting",
                "recommendations": {
                    "use_simple_formatting": True,
                    "reason": "Cannot load HF metadata, use simple message concatenation",
                },
            }
        )
        return analysis

    # Check for chat template in tokenizer
    if meta.chat_template:
        analysis.update(
            {
                "has_chat_template": True,
                "template_type": "hf_tokenizer",
                "chat_template": meta.chat_template,
                "tokenizer_available": True,
                "recommendations": {
                    "use_hf_template": True,
                    "reason": "Model has built-in chat template in tokenizer",
                },
            }
        )
        return analysis

    # Check for specific model types that might have chat templates
    model_type = detect_model_type_from_metadata(meta)
    if model_type in ["qwen", "llama", "mistral", "mixtral", "codellama", "deepseek"]:
        analysis.update(
            {
                "template_type": "model_specific",
                "recommendations": {
                    "use_model_specific_template": True,
                    "reason": f"Model type {model_type} typically supports chat templates",
                },
            }
        )
        return analysis

    # Fallback to simple formatting
    analysis.update(
        {
            "template_type": "simple_formatting",
            "recommendations": {
                "use_simple_formatting": True,
                "reason": "No chat template detected, use simple message concatenation",
            },
        }
    )

    return analysis


def analyze_model_capabilities(
    model_path: str, meta: HFMetadataLite | None = None
) -> dict[str, Any]:
    """Analyze HF model capabilities and configuration."""
    if meta is None:
        meta = load_hf_metadata(model_path)

    capabilities = {
        "model_path": model_path,
        "directory_exists": os.path.exists(model_path),
        "total_size_mb": 0,
        "estimated_parameters": "unknown",
        "format": "hf",
        "chat_template_analysis": None,
        "recommended_config": {},
    }

    if capabilities["directory_exists"]:
        try:
            total_size = 0
            for root, dirs, files in os.walk(model_path):
                for file in files:
                    file_path = os.path.join(root, file)
                    total_size += os.path.getsize(file_path)
            capabilities["total_size_mb"] = round(total_size / (1024 * 1024), 2)
        except Exception as e:
            capabilities["size_error"] = str(e)

    # Add metadata information if available
    if meta:
        capabilities.update(
            {
                "metadata": {
                    "name": meta.name,
                    "architecture": meta.architecture,
                    "context_length": meta.context_length,
                    "hidden_size": meta.hidden_size,
                    "num_layers": meta.num_layers,
                    "num_attention_heads": meta.num_attention_heads,
                    "num_key_value_heads": meta.num_key_value_heads,
                    "vocab_size": meta.vocab_size,
                    "model_type": meta.model_type,
                    "tokenizer_class": meta.tokenizer_class,
                    "torch_dtype": meta.torch_dtype,
                    "chat_template_available": bool(meta.chat_template),
                }
            }
        )

        # Estimate parameters from metadata
        if meta.num_layers > 0 and meta.hidden_size > 0:
            # Rough parameter estimation for transformer models
            # Parameters ≈ 12 * layers * hidden_size^2 + 2 * vocab_size * hidden_size
            params_estimate = (
                12 * meta.num_layers * (meta.hidden_size**2)
                + 2 * meta.vocab_size * meta.hidden_size
            )
            if params_estimate > 1e9:
                capabilities["estimated_parameters"] = f"~{params_estimate / 1e9:.1f}B"
            elif params_estimate > 1e6:
                capabilities["estimated_parameters"] = f"~{params_estimate / 1e6:.0f}M"

    # Analyze chat template support
    capabilities["chat_template_analysis"] = detect_chat_template_support(
        model_path, meta
    )

    return capabilities


def validate_model_requirements(
    model_path: str, meta: HFMetadataLite | None = None
) -> dict[str, Any]:
    """Validate that model meets requirements for HF engine."""
    if meta is None:
        meta = load_hf_metadata(model_path)

    validation = {"valid": True, "errors": [], "warnings": [], "requirements_met": True}

    # Check directory exists
    if not os.path.exists(model_path):
        validation["errors"].append(f"Model directory not found: {model_path}")
        validation["valid"] = False
        return validation

    # Check for required files
    required_files = ["config.json"]
    optional_files = [
        "tokenizer.json",
        "tokenizer_config.json",
        "vocab.json",
        "merges.txt",
    ]

    for file in required_files:
        if not os.path.exists(os.path.join(model_path, file)):
            validation["errors"].append(f"Required file not found: {file}")
            validation["valid"] = False

    for file in optional_files:
        if not os.path.exists(os.path.join(model_path, file)):
            validation["warnings"].append(f"Optional file not found: {file}")

    # Check for model files
    model_files = [
        f
        for f in os.listdir(model_path)
        if f.endswith((".bin", ".safetensors", ".pt", ".pth"))
    ]
    if not model_files:
        validation["warnings"].append(
            "No model weight files found (.bin, .safetensors, .pt, .pth)"
        )

    # Check metadata loading
    if meta is None:
        validation["warnings"].append(
            "Could not load HF metadata - may indicate format issues"
        )
    else:
        # Validate metadata content
        if meta.architecture == "unknown":
            validation["warnings"].append("Model architecture not recognized")
        if meta.context_length <= 0:
            validation["warnings"].append("Context length not specified or invalid")
        if meta.num_layers <= 0:
            validation["warnings"].append("Number of layers not specified or invalid")

    # Check directory size
    try:
        total_size = 0
        for root, dirs, files in os.walk(model_path):
            for file in files:
                file_path = os.path.join(root, file)
                total_size += os.path.getsize(file_path)

        if total_size < 10 * 1024 * 1024:  # < 10MB
            validation["warnings"].append(
                "Directory size is very small - may not be a complete model"
            )
        elif total_size > 100 * 1024 * 1024 * 1024:  # > 100GB
            validation["warnings"].append(
                "Directory size is very large - may require special handling"
            )
    except Exception as e:
        validation["errors"].append(f"Could not check directory size: {e}")

    if validation["errors"]:
        validation["valid"] = False
        validation["requirements_met"] = False

    return validation


def get_model_info_summary(model_path: str) -> dict[str, Any]:
    """Get complete model information summary for HF model."""
    logger.debug("Loading HF metadata")
    meta = load_hf_metadata(model_path)

    logger.debug("Detecting model type from metadata")
    model_type = detect_model_type_from_metadata(meta) if meta else "unknown"

    logger.debug("Analyzing model capabilities")
    capabilities = analyze_model_capabilities(model_path, meta)

    logger.debug("Detecting chat template support")
    chat_template = detect_chat_template_support(model_path, meta)

    logger.debug("Validating model requirements")
    validation = validate_model_requirements(model_path, meta)

    logger.debug("Assembling model info dictionary")
    info = {
        "model_path": model_path,
        "model_type": model_type,
        "capabilities": capabilities,
        "chat_template": chat_template,
        "validation": validation,
        "metadata": meta.to_dict() if meta else None,
        "engine": "hf",
    }

    return info


def format_file_size(size_bytes: int) -> str:
    """Format file size in human-readable format."""
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if size_bytes < 1024.0:
            return f"{size_bytes:.2f} {unit}"
        size_bytes /= 1024.0
    return f"{size_bytes:.2f} PB"


def print_section_header(title: str, width: int = 80):
    """Print a formatted section header."""
    print("\n" + "=" * width)
    print(f" {title}")
    print("=" * width)


def print_subsection_header(title: str, width: int = 80):
    """Print a formatted subsection header."""
    print(f"\n{'-' * width}")
    print(f" {title}")
    print("-" * width)


def print_key_value(key: str, value: Any, indent: int = 0):
    """Print a key-value pair with consistent formatting."""
    indent_str = " " * indent
    if isinstance(value, bool):
        value_str = "✓" if value else "✗"
    elif value is None:
        value_str = "N/A"
    else:
        value_str = str(value)
    print(f"{indent_str}{key}: {value_str}")


def print_chat_template_analysis(analysis: dict[str, Any]):
    """Print detailed chat template analysis."""
    print_subsection_header("Chat Template Analysis")

    print_key_value("Has Chat Template", analysis.get("has_chat_template", False))
    print_key_value("Template Type", analysis.get("template_type", "unknown"))

    chat_template = analysis.get("chat_template")
    if chat_template:
        print_key_value("Template Content", chat_template)
        print("\nTemplate Usage:")
        if "hf_tokenizer" in analysis.get("template_type", ""):
            print("  • Use the built-in HF chat template")
        else:
            print("  • Use simple message concatenation")

    recommendations = analysis.get("recommendations", {})
    if recommendations:
        print("\nRecommendations:")
        for key, value in recommendations.items():
            if isinstance(value, bool):
                value_str = "✓" if value else "✗"
            else:
                value_str = str(value)
            print(f"  • {key}: {value_str}")


def print_capabilities_analysis(capabilities: dict[str, Any]):
    """Print detailed capabilities analysis."""
    print_subsection_header("Model Capabilities")

    print_key_value("Directory Exists", capabilities.get("directory_exists", False))
    if capabilities.get("total_size_mb"):
        print_key_value("Total Size", f"{capabilities['total_size_mb']} MB")
    print_key_value("Format", capabilities.get("format", "unknown"))
    print_key_value(
        "Estimated Parameters", capabilities.get("estimated_parameters", "unknown")
    )

    metadata = capabilities.get("metadata")
    if metadata:
        print("\nMetadata:")
        for key, value in metadata.items():
            print_key_value(key, value, indent=2)

    recommended_config = capabilities.get("recommended_config", {})
    if recommended_config:
        print("\nRecommended Configuration:")
        for key, value in recommended_config.items():
            print_key_value(key, value, indent=2)


def print_validation_results(validation: dict[str, Any]):
    """Print validation results."""
    print_subsection_header("Validation Results")

    print_key_value("Valid", validation.get("valid", False))
    print_key_value("Requirements Met", validation.get("requirements_met", False))

    errors = validation.get("errors", [])
    if errors:
        print("\nErrors:")
        for error in errors:
            print(f"  ✗ {error}")

    warnings = validation.get("warnings", [])
    if warnings:
        print("\nWarnings:")
        for warning in warnings:
            print(f"  ⚠ {warning}")


def print_model_summary(info: dict[str, Any]):
    """Print a concise model summary."""
    print_section_header("Model Summary")

    print_key_value("Model Path", info.get("model_path", "N/A"))
    print_key_value("Model Type", info.get("model_type", "unknown"))
    print_key_value("Engine", info.get("engine", "unknown"))

    capabilities = info.get("capabilities", {})
    if capabilities:
        print_key_value("Total Size", f"{capabilities.get('total_size_mb', 0)} MB")
        print_key_value(
            "Estimated Parameters", capabilities.get("estimated_parameters", "unknown")
        )

        metadata = capabilities.get("metadata", {})
        if metadata:
            print_key_value("Architecture", metadata.get("architecture", "unknown"))
            print_key_value("Context Length", metadata.get("context_length", "unknown"))
            # Check chat template from the actual analysis, not just metadata
            chat_template_analysis = capabilities.get("chat_template_analysis", {})
            has_chat_template = chat_template_analysis.get(
                "has_chat_template", metadata.get("chat_template_available", False)
            )
            print_key_value("Has Chat Template", has_chat_template)

    validation = info.get("validation", {})
    if validation:
        print_key_value("Valid", validation.get("valid", False))


def test_vllm_memory_usage(
    model_path: str,
    max_model_len: int,
    gpu_memory_utilization: float,
    gpu_index: int = 0,
) -> tuple[bool, int | None, int | None]:
    """
    Load model with vLLM in subprocess and measure RAM/VRAM usage.

    Compilation behavior controlled via TORCH_COMPILE_DISABLE environment variable.

    Args:
        model_path: Path to HF model directory
        max_model_len: Maximum sequence length to test
        gpu_memory_utilization: GPU memory utilization setting
        gpu_index: GPU device index

    Returns:
        (success, ram_mb, vram_mb) tuple
    """
    test_script = Path(__file__).parent / "vllm_test_scripts" / "vllm_memory_test.py"

    if not test_script.exists():
        logger.warning(f"Test script not found: {test_script}")
        return (False, None, None)

    try:
        env = os.environ.copy()
        env["CUDA_VISIBLE_DEVICES"] = str(gpu_index)

        # Set optimal environment for RTX 5090 / SM_120
        # Compilation disabled via TORCH_COMPILE_DISABLE
        env["TORCH_CUDA_ARCH_LIST"] = "12.0"
        env["VLLM_ATTENTION_BACKEND"] = "FLASH_ATTN"
        env["VLLM_USE_TRITON_FLASH_ATTN"] = "0"
        env["TORCH_COMPILE_DISABLE"] = "1"

        cmd = [
            sys.executable,
            str(test_script),
            "--model",
            model_path,
            "--max-model-len",
            str(max_model_len),
            "--gpu-memory-utilization",
            str(gpu_memory_utilization),
        ]

        # Don't pass --quantization flag, let vLLM auto-detect from config.json

        import subprocess

        result = subprocess.run(
            cmd,
            env=env,
            capture_output=True,
            timeout=300,  # 5 minute timeout per test
            text=True,
        )

        # Parse JSON output from subprocess
        if result.returncode == 0 and result.stdout.strip():
            try:
                # The new memory test script outputs clean JSON on stdout
                # and debug messages on stderr, making parsing much simpler
                stdout_content = result.stdout.strip()

                # Find the JSON line (should be the last non-empty line)
                json_line = None
                for line in reversed(stdout_content.split("\n")):
                    line = line.strip()
                    if line and line.startswith("{") and line.endswith("}"):
                        try:
                            # Test if this line is valid JSON
                            json.loads(line)
                            json_line = line
                            break
                        except json.JSONDecodeError:
                            continue

                if json_line:
                    data = json.loads(json_line)
                    success = data.get("success", False)

                    # Check if there's an error in the JSON response
                    if not success and "error" in data:
                        logger.error(f"vLLM load failed: {data['error']}")

                    # Log debug info from stderr if available
                    if result.stderr.strip():
                        logger.debug(f"Memory test debug output: {result.stderr}")

                    return (success, data.get("ram_mb"), data.get("vram_mb"))
                else:
                    logger.error("No valid JSON found in subprocess output")
                    logger.error(f"stdout: {result.stdout}")
                    if result.stderr.strip():
                        logger.error(f"stderr: {result.stderr}")
                    return (False, None, None)

            except json.JSONDecodeError as e:
                logger.error(f"Failed to parse JSON output: {e}")
                logger.error(f"stdout: {result.stdout}")
                if result.stderr.strip():
                    logger.error(f"stderr: {result.stderr}")
                return (False, None, None)

        # Log the failure details
        logger.error(f"Memory test subprocess failed (returncode={result.returncode})")
        if result.stdout.strip():
            logger.error(f"stdout: {result.stdout}")
        if result.stderr.strip():
            logger.error(f"stderr: {result.stderr}")

        return (False, None, None)

    except subprocess.TimeoutExpired:
        logger.error("Memory test timed out after 300 seconds")
        return (False, None, None)
    except Exception as e:
        logger.error(f"Memory test failed with exception: {e}")
        import traceback

        logger.error(traceback.format_exc())
        return (False, None, None)


def generate_hf_profiles(
    base_max_len: int,
    context_lengths: list[int],
    model_path: str,
    skip_memory_test: bool,
    gpu_index: int,
    gpu_memory_utilization: float = 0.98,
    format_type: str = "hf",
) -> tuple[dict[str, dict], bool]:
    """
    Generate profiles for different max_model_len values.

    Similar to GGUF profiles but for vLLM max_model_len instead of n_ctx/n_gpu_layers.

    Args:
        base_max_len: Base context length from model config (required)
        context_lengths: List of context lengths to generate profiles for
        model_path: Path to model for memory testing
        skip_memory_test: If True, skip memory measurement
        gpu_index: GPU device index for testing
        gpu_memory_utilization: GPU memory utilization ratio (0.0-1.0)

    Returns:
        Tuple of (profiles_dict, all_tests_successful)
        - profiles_dict: Dict with string keys like "8192", "16384" mapping to profile configs
        - all_tests_successful: True if all memory tests succeeded, False if any failed
    """
    # Use GPU memory utilization from parameter
    gpu_mem_util = gpu_memory_utilization

    profiles = {}
    any_test_succeeded = False

    # Sort context lengths
    sorted_contexts = sorted(context_lengths)

    for ctx_len in sorted_contexts:
        # Skip if context length exceeds model's native context
        if ctx_len > base_max_len:
            continue

        profile = {
            "loader": {"max_model_len": ctx_len},
            "resources": {"ram_mb": None, "vram_mb": None},
        }

        # Measure memory if not skipped
        if not skip_memory_test:
            logger.info(f"Testing memory usage for max_model_len={ctx_len}...")
            success, ram_mb, vram_mb = test_vllm_memory_usage(
                model_path, ctx_len, gpu_mem_util, gpu_index
            )

            if success:
                profile["resources"]["ram_mb"] = ram_mb
                profile["resources"]["vram_mb"] = vram_mb
                logger.info(f"  RAM: {ram_mb}MB, VRAM: {vram_mb}MB")
                any_test_succeeded = True
            else:
                logger.error(
                    f"  Memory test failed for max_model_len={ctx_len} (likely insufficient GPU memory for KV cache)"
                )

            # Add delay between memory tests to allow GPU cleanup
            import time

            time.sleep(2)  # 2 second delay to allow GPU memory to be released

        profiles[str(ctx_len)] = profile

    # If memory tests were requested but none succeeded, return failure
    if not skip_memory_test and not any_test_succeeded:
        return profiles, False

    # If tests were skipped or at least one succeeded, return success
    return profiles, True


def generate_standardized_yaml(
    model_path: str,
    info: dict[str, Any],
    use_profiles: bool = False,
    context_lengths: list[int] | None = None,
    skip_memory_test: bool = False,
    gpu_index: int = 0,
    gpu_memory_utilization: float = 0.98,
) -> dict[str, Any]:
    """Generate standardized YAML output format for model configuration."""

    # Extract model name from path
    model_name = os.path.basename(model_path)

    # Create a standardized model identifier
    model_id = model_name.lower().replace(" ", "-").replace("_", "-").replace(".", "-")
    model_id = "-".join(filter(None, model_id.split("-")))

    # Get basic info from analysis
    capabilities = info.get("capabilities", {})
    metadata = capabilities.get("metadata", {})
    chat_template_analysis = capabilities.get("chat_template_analysis", {})

    # Get actual max_model_len from config (required)
    base_max_len = get_max_model_len_from_config(model_path)
    if base_max_len is None:
        raise ValueError("Could not determine max_model_len from model config")

    # Detect format and quantization
    format_type, quant_bits = detect_quantization_format(model_path)

    # Set quant field based on format
    if format_type == "awq":
        quant_str = f"awq-{quant_bits}bit" if quant_bits else "awq-4bit"
    elif format_type == "gptq":
        quant_str = f"gptq-{quant_bits}bit" if quant_bits else "gptq-4bit"
    else:
        quant_str = None

    # Use GPU memory utilization from parameter
    gpu_mem_util = gpu_memory_utilization

    # Determine model family and architecture
    model_family = None
    architecture = None

    if metadata:
        arch = metadata.get("architecture", "").lower()
        model_type = metadata.get("model_type", "").lower()

        if "llama" in arch or "llama" in model_type:
            model_family = "llama"
            architecture = "llama"
        elif "mistral" in arch or "mistral" in model_type:
            model_family = "mistral"
            architecture = "mistral"
        elif "mixtral" in arch or "mixtral" in model_type:
            model_family = "mixtral"
            architecture = "mixtral-8x7b"
        elif "qwen" in arch or "qwen" in model_type:
            model_family = "qwen"
            architecture = "qwen"
        elif "codellama" in arch or "codellama" in model_type:
            model_family = "codellama"
            architecture = "codellama"
        elif "deepseek" in arch or "deepseek" in model_type:
            model_family = "deepseek"
            architecture = "deepseek"
        elif "phi" in arch or "phi" in model_type:
            model_family = "phi"
            architecture = "phi"
        elif "gemma" in arch or "gemma" in model_type:
            model_family = "gemma"
            architecture = "gemma"
        elif "falcon" in arch or "falcon" in model_type:
            model_family = "falcon"
            architecture = "falcon"
        elif "bloom" in arch or "bloom" in model_type:
            model_family = "bloom"
            architecture = "bloom"

    # Determine chat template support
    supports_chat_history = chat_template_analysis.get("has_chat_template", False)

    # Build the info section (gateway schema)
    info_section = {
        "name": model_name,
        "format": format_type,  # "hf", "gptq", or "awq"
        "path": model_path,
        "enabled": True,
        "family": model_family or "unknown",
        "arch": architecture or "unknown",
        "quant": quant_str,
        "license": None,
        "parameters": None,
        "training_cutoff_year": None,
        "training_context_length": base_max_len,
        "release_date": None,
        "supports_chat_history": supports_chat_history,
        "input_schema": "messages" if supports_chat_history else "prompt",
        "description": None,
        "capabilities": None,
        "safety_info": None,
        "openai_api_fields": {
            "id": model_id,
            "object": "model",
            "owned_by": "universal-llm-gateway",
            "permission": ["generate"],
        },
    }

    # Detect RoPE scaling for sliding window configuration
    has_rope_scaling = detect_rope_scaling(model_path)

    # Determine if we can safely disable sliding window
    # AWQ models with RoPE scaling cannot have sliding window disabled
    can_disable_sliding_window = not (format_type == "awq" and has_rope_scaling)

    # Build base loader config
    base_loader = {
        "trust_remote_code": False,
        "gpu_memory_utilization": gpu_mem_util,
        "max_model_len": base_max_len,
        "dtype": "float16"
        if format_type == "gptq"
        else "auto",  # GPTQ requires float16
        "disable_custom_all_reduce": True,
        "disable_log_stats": True,
        # Conditionally disable sliding window based on model compatibility
        "disable_sliding_window": can_disable_sliding_window,
    }

    # Build the model configuration
    model_config = {"info": info_section, "base_loader": base_loader, "profiles": {}}

    # Add profiles if requested
    if use_profiles and context_lengths:
        # Filter context lengths to only include values <= base_max_len
        filtered_contexts = [ctx for ctx in context_lengths if ctx <= base_max_len]

        if filtered_contexts:
            profiles, memory_tests_successful = generate_hf_profiles(
                base_max_len,
                filtered_contexts,
                model_path,
                skip_memory_test,
                gpu_index,
                gpu_memory_utilization,
                format_type,
            )

            # If memory tests were requested and all failed, exit early
            if not skip_memory_test and not memory_tests_successful:
                print(
                    "\n❌ All memory tests failed. Cannot generate reliable configuration."
                )
                print(
                    "Try using --skip-memory-test if you want to generate config without memory measurements."
                )
                sys.exit(1)

            if profiles:
                model_config["profiles"].update(profiles)

    # Build the complete gateway-compatible output with resource management and models wrapper
    standardized_output = {
        "resource_management": {"max_concurrent_models": 100},
        "models": {model_id: model_config},
    }

    return standardized_output


def compute_cache_key(model_path: str) -> str:
    """Compute cache key for model configuration (single cache per model)."""
    # Get model directory hash (first and last 4KB of config.json for speed)
    config_path = os.path.join(model_path, "config.json")
    try:
        with open(config_path, "rb") as f:
            f.seek(0)
            start = f.read(4096)
            f.seek(-4096, 2)
            end = f.read(4096)
            model_hash = hashlib.sha256(start + end).hexdigest()[:16]
    except Exception:
        model_hash = hashlib.sha256(model_path.encode()).hexdigest()[:16]

    # Get GPU info
    gpu_available = torch.cuda.is_available() if torch_available else False
    if gpu_available:
        gpu_name = torch.cuda.get_device_name(0)
        total_vram = torch.cuda.get_device_properties(0).total_memory
    else:
        gpu_name = "none"
        total_vram = "0"

    transformers_ver = (
        transformers_version if transformers_available else "not-installed"
    )

    # Create key (single cache per model + GPU + transformers version)
    key_str = f"{model_hash}_{transformers_ver}_{gpu_name}_{total_vram}"
    return hashlib.sha256(key_str.encode()).hexdigest()


def get_capacity_cache_path(cache_key: str) -> Path:
    """Get path to capacity cache file."""
    cache_dir = Path.home() / ".cache" / "inference_djinn" / "vllm_capacity"
    cache_dir.mkdir(parents=True, exist_ok=True)
    return cache_dir / f"{cache_key}.json"


def save_config_to_cache(cache_key: str, config: dict[str, Any]) -> None:
    """Save complete model configuration to cache."""
    cache_path = get_capacity_cache_path(cache_key)
    try:
        # Extract path from config (handle new gateway format)
        model_path = None
        models = config.get("models", {})
        if models:
            # New gateway format
            model_config = next(iter(models.values()))
            model_path = model_config.get("info", {}).get("path")
        else:
            # Old format fallback
            model_path = config.get("info", {}).get("path")

        timestamp = None
        if model_path:
            try:
                config_json_path = os.path.join(model_path, "config.json")
                if os.path.exists(config_json_path):
                    timestamp = os.path.getmtime(config_json_path)
            except Exception:
                pass

        with open(cache_path, "w") as f:
            json.dump({"config": config, "timestamp": timestamp}, f, indent=2)
    except Exception as e:
        logger.warning(f"Failed to save cache: {e}")


def get_latest_cached_config(
    model_path: str,
    context_lengths: list[int] | None = None,
    api_url: str | None = None,
    api_token: str | None = None,
    model_key: str | None = None,
) -> dict[str, Any] | None:
    """
    Get the latest cached configuration for a model.

    Args:
        model_path: Path to the model directory
        context_lengths: List of context lengths to look for (used for filtering profiles)
        api_url: Optional API URL for gateway lookup
        api_token: Optional API token for gateway lookup
        model_key: Optional model key to use

    Returns:
        Complete configuration dict if found, None otherwise
    """
    try:
        # Compute cache key for this model
        cache_key = compute_cache_key(model_path)
        cache_path = get_capacity_cache_path(cache_key)

        if not cache_path.exists():
            logger.info(f"No cache found for model: {os.path.basename(model_path)}")
            logger.info("Run without --use-cached to generate fresh config")
            return None

        # Load cached configuration
        with open(cache_path) as f:
            cache_data = json.load(f)

        if "config" not in cache_data:
            logger.info("Invalid cache format, regenerating config...")
            return None

        config = cache_data["config"]

        # Handle both old and new formats
        models = config.get("models", {})
        if models:
            # New gateway format
            model_id, model_config = next(iter(models.items()))

            # Filter profiles to only include requested contexts
            if context_lengths and "profiles" in model_config:
                filtered_profiles = {}
                for ctx in context_lengths:
                    ctx_str = str(ctx)
                    if ctx_str in model_config["profiles"]:
                        filtered_profiles[ctx_str] = model_config["profiles"][ctx_str]

                if not filtered_profiles:
                    logger.info(
                        f"No profiles found for requested contexts {context_lengths}"
                    )
                    return None

                model_config["profiles"] = filtered_profiles

                # Remove any existing default fields from profiles (schema no longer supports them)
                for profile in model_config["profiles"].values():
                    if "default" in profile:
                        del profile["default"]

            # Check if gateway has an existing model with this path and use its key
            if api_url and not model_key:
                existing_model_key = get_existing_model_key_for_path(
                    model_path, api_url, api_token
                )
                if existing_model_key:
                    logger.info(
                        f"Found existing model '{existing_model_key}' in gateway for this path, using its key"
                    )
                    # Update both the models key and the openai_api_fields id
                    del config["models"][model_id]
                    config["models"][existing_model_key] = model_config
                    model_config["info"]["openai_api_fields"]["id"] = existing_model_key
                else:
                    logger.info(
                        "No existing model found in gateway for this path, using generated key"
                    )
            elif model_key:
                logger.info(f"Using user-specified model key: {model_key}")
                # Update both the models key and the openai_api_fields id
                del config["models"][model_id]
                config["models"][model_key] = model_config
                model_config["info"]["openai_api_fields"]["id"] = model_key

        elif "info" in config:
            # Old format - convert to new format
            logger.info(
                "Converting cached config from old format to new gateway format"
            )

            # Get model key from old format
            old_model_key = (
                config.get("info", {})
                .get("openai_api_fields", {})
                .get("id", "unknown-model")
            )

            # Filter profiles to only include requested contexts
            if context_lengths and "profiles" in config:
                filtered_profiles = {}
                for ctx in context_lengths:
                    ctx_str = str(ctx)
                    if ctx_str in config["profiles"]:
                        filtered_profiles[ctx_str] = config["profiles"][ctx_str]

                if not filtered_profiles:
                    logger.info(
                        f"No profiles found for requested contexts {context_lengths}"
                    )
                    return None

                config["profiles"] = filtered_profiles

                # Remove any existing default fields from profiles (schema no longer supports them)
                for profile in config["profiles"].values():
                    if "default" in profile:
                        del profile["default"]

            # Update model key
            if api_url and not model_key:
                existing_model_key = get_existing_model_key_for_path(
                    model_path, api_url, api_token
                )
                if existing_model_key:
                    logger.info(
                        f"Found existing model '{existing_model_key}' in gateway for this path, using its key"
                    )
                    config["info"]["openai_api_fields"]["id"] = existing_model_key
                    old_model_key = existing_model_key
                else:
                    logger.info(
                        "No existing model found in gateway for this path, using generated key"
                    )
            elif model_key:
                logger.info(f"Using user-specified model key: {model_key}")
                config["info"]["openai_api_fields"]["id"] = model_key
                old_model_key = model_key

            # Convert to new gateway format
            config = {
                "resource_management": {"max_concurrent_models": 100},
                "models": {
                    old_model_key: {
                        "info": config["info"],
                        "base_loader": config.get("base_loader", {}),
                        "profiles": config.get("profiles", {}),
                    }
                },
            }

        else:
            logger.info("Invalid cache format, regenerating config...")
            return None

        # Count profiles from the gateway format
        models = config.get("models", {})
        profile_count = 0
        if models:
            model_config = next(iter(models.values()))
            profile_count = len(model_config.get("profiles", {}))

        logger.info(f"Loaded cached configuration with {profile_count} profiles")
        return config

    except Exception as e:
        logger.warning(f"Failed to load cached config: {e}")
        return None


def get_existing_model_key_for_path(
    model_path: str, api_url: str, api_token: str | None
) -> str | None:
    """
    Get the existing model key for a given path from the gateway.

    Args:
        model_path: The model directory path to look for
        api_url: Base URL of the API
        api_token: Authentication token (optional)

    Returns:
        Existing model key if found, None otherwise
    """
    try:
        import requests
    except ImportError:
        return None

    headers = {}
    if api_token:
        headers["X-Management-Token"] = api_token

    try:
        # Try to get model info by attempting a POST and parsing the response
        test_config = {"info": {"path": model_path}, "profiles": {}}

        payload = {
            "model_key": "temp-lookup-key",
            "config": test_config,
            "allow_overwrite": False,
        }

        response = requests.post(
            f"{api_url}/api/v1/models", json=payload, headers=headers, timeout=10
        )

        if response.status_code in (
            200,
            201,
            409,
        ):  # Success or conflict - model exists
            response_text = response.text
            # Look for pattern like "Updated existing model 'qwen-7b-chat'"
            import re

            pattern = r"Updated existing model '([^']+)'"
            match = re.search(pattern, response_text)
            if match:
                return match.group(1)

            # Look for pattern in error message
            pattern2 = r"found in models '([^']+)'"
            match2 = re.search(pattern2, response_text)
            if match2:
                return match2.group(1)

        return None
    except Exception:
        return None


def fetch_schema_from_api(api_url: str) -> dict[str, Any] | None:
    """Fetch HF/vLLM schema from universal-llm-gateway API."""
    try:
        import requests
    except ImportError:
        logger.warning("requests not available, skipping API validation")
        return None

    endpoint = f"{api_url.rstrip('/')}/api/v1/models/schemas/hf"
    logger.info(f"Fetching schema from: {endpoint}")

    try:
        response = requests.get(endpoint, timeout=10)
        if response.status_code != 200:
            raise RuntimeError(
                f"API returned status {response.status_code}: {response.text}"
            )

        data = response.json()

        # Validate expected fields
        if "schema_fields" not in data or "required_fields" not in data:
            raise RuntimeError(
                f"API response missing expected fields. Got keys: {list(data.keys())}"
            )

        return data

    except requests.exceptions.RequestException as e:
        raise RuntimeError(f"Failed to fetch schema from API: {e}")


def validate_against_schema(
    config: dict[str, Any], schema_data: dict[str, Any]
) -> None:
    """Validate full config against schema and print warnings if drift detected."""
    schema_fields = set(schema_data.get("schema_fields", []))
    required_fields = set(schema_data.get("required_fields", []))

    # Recursively get all fields from config
    def get_all_fields(d, prefix=""):
        fields = set()
        for k, v in d.items():
            field_name = f"{prefix}.{k}" if prefix else k
            fields.add(field_name)
            if isinstance(v, dict) and not field_name.startswith("profiles."):
                # Recurse into dicts, but don't recurse into profile values
                fields.update(get_all_fields(v, field_name))
        return fields

    our_fields = get_all_fields(config)

    # Check for drift
    extra_fields = our_fields - schema_fields
    missing_required = required_fields - our_fields

    if extra_fields or missing_required:
        print("\n⚠️  Schema Drift Detected:", file=sys.stderr)
        if extra_fields:
            print(
                f"  Fields we provide but not in schema: {sorted(extra_fields)}",
                file=sys.stderr,
            )
        if missing_required:
            print(
                f"  Required fields we're missing: {sorted(missing_required)}",
                file=sys.stderr,
            )
            raise ValueError(
                f"Missing required schema fields: {sorted(missing_required)}"
            )
        print(
            "  Continuing anyway (extra fields may be ignored by gateway)...",
            file=sys.stderr,
        )


def push_to_api(
    config: dict,
    model_key: str,
    api_url: str,
    api_token: str | None,
    update: bool = False,
) -> bool:
    """
    Push configuration to universal-llm-gateway API.

    Args:
        config: The model configuration to push
        model_key: The model key/ID for the API
        api_url: Base URL of the API
        api_token: Authentication token (optional)
        update: If True, use PUT to update existing model; if False, use POST with allow_overwrite=True

    Returns:
        True if successful, False otherwise
    """
    try:
        import requests
    except ImportError:
        logger.error(
            "'requests' library not installed. Install with: pip install requests"
        )
        return False

    # Use PUT for explicit updates, POST for create/update behavior
    if update:
        url = f"{api_url}/api/v1/models/{model_key}"
        method = "PUT"
    else:
        url = f"{api_url}/api/v1/models"
        method = "POST"

    # Prepare headers
    headers = {"Content-Type": "application/json"}
    if api_token:
        headers["X-Management-Token"] = api_token

    # Prepare payload based on method
    if update:
        # PUT request - just send the config
        payload = {"config": config}
    else:
        # POST request - include model_key and allow_overwrite
        payload = {"model_key": model_key, "config": config, "allow_overwrite": True}

    logger.info("\n🚀 Pushing configuration to API...")
    logger.info(f"   URL: {url}")
    logger.info(f"   Method: {method}")
    logger.info(f"   Model Key: {model_key}")
    if not update:
        logger.info("   Allow Overwrite: True")

    try:
        if update:
            response = requests.put(url, json=payload, headers=headers, timeout=30)
        else:
            response = requests.post(url, json=payload, headers=headers, timeout=30)

        if response.status_code in (200, 201):
            result = response.json()
            status = result.get("status", "success")
            if status == "updated":
                logger.info(f"✅ Updated existing model: {model_key}")
            elif status == "created":
                logger.info(f"✅ Created new model: {model_key}")
            else:
                logger.info(f"✅ {result.get('message', 'Success')}")
            return True
        else:
            logger.error(f"❌ API Error ({response.status_code}): {response.text}")
            return False

    except requests.exceptions.ConnectionError:
        logger.error(f"❌ Connection Error: Could not connect to {api_url}")
        logger.error(
            "   Make sure the gateway is running and ENABLE_MANAGEMENT_API=true"
        )
        return False
    except Exception as e:
        logger.error(f"❌ Request failed: {e}")
        return False


def print_config_summary(config: dict[str, Any]) -> None:
    """Print configuration summary to stderr (similar to GGUF)."""
    # Handle new gateway format with models wrapper
    models = config.get("models", {})
    if not models:
        # Fallback to old format
        info = config.get("info", {})
        profiles = config.get("profiles", {})
        base_loader = config.get("base_loader", {})
    else:
        # Extract the first (and should be only) model from the models dict
        model_config = next(iter(models.values()))
        info = model_config.get("info", {})
        profiles = model_config.get("profiles", {})
        base_loader = model_config.get("base_loader", {})

    print("\n" + "=" * 70, file=sys.stderr)
    print("vLLM Model Configuration Summary", file=sys.stderr)
    print("=" * 70, file=sys.stderr)

    print(f"\nModel: {info.get('name')}", file=sys.stderr)
    print(f"  Format: {info.get('format')}", file=sys.stderr)
    print(f"  Family: {info.get('family')}", file=sys.stderr)
    print(f"  Architecture: {info.get('arch')}", file=sys.stderr)

    if info.get("parameters"):
        params_b = info["parameters"] / 1_000_000_000
        print(f"  Parameters: {params_b:.1f}B", file=sys.stderr)

    if info.get("quant"):
        print(f"  Quantization: {info['quant']}", file=sys.stderr)

    if info.get("training_context_length"):
        print(
            f"  Training Context: {info['training_context_length']:,}", file=sys.stderr
        )

    # Print profiles or base_loader info
    if profiles:
        print(f"\nProfiles Generated: {len(profiles)}", file=sys.stderr)
        for ctx_str, profile in sorted(profiles.items(), key=lambda x: int(x[0])):
            ctx = int(ctx_str)
            max_len = profile.get("loader", {}).get("max_model_len", ctx)
            ram_mb = profile.get("resources", {}).get("ram_mb")
            vram_mb = profile.get("resources", {}).get("vram_mb")

            resource_str = ""
            if ram_mb is not None or vram_mb is not None:
                parts = []
                if ram_mb is not None:
                    parts.append(f"RAM: {ram_mb}MB")
                if vram_mb is not None:
                    parts.append(f"VRAM: {vram_mb}MB")
                resource_str = f" ({', '.join(parts)})"

            print(
                f"  {ctx:>7,}: max_model_len={max_len}{resource_str}", file=sys.stderr
            )
    elif base_loader:
        print("\nBase Loader Configuration:", file=sys.stderr)
        print(f"  max_model_len: {base_loader.get('max_model_len')}", file=sys.stderr)
        print(
            f"  gpu_memory_utilization: {base_loader.get('gpu_memory_utilization')}",
            file=sys.stderr,
        )

    print("\n" + "=" * 70, file=sys.stderr)


def print_standardized_yaml(yaml_output: dict[str, Any]):
    """Print the standardized YAML output in a readable format."""

    # Custom YAML dumper to handle model names properly
    class ModelNameDumper(yaml.SafeDumper):
        def represent_str(self, data):
            # Quote strings that contain special characters
            if any(char in data for char in ":.()[]{}|>"):
                return self.represent_scalar("tag:yaml.org,2002:str", data, style='"')
            return self.represent_scalar("tag:yaml.org,2002:str", data)

        def represent_mapping(self, tag, mapping, flow_style=None):
            # Quote keys that contain special characters
            value = []
            node = yaml.MappingNode(tag, value, flow_style=flow_style)
            if self.alias_key is not None:
                self.represented_objects[self.alias_key] = node
            if hasattr(mapping, "items"):
                mapping = list(mapping.items())
            for item_key, item_value in mapping:
                node_key = self.represent_data(item_key)
                node_value = self.represent_data(item_value)
                # Quote keys that contain special characters
                if isinstance(item_key, str) and any(
                    char in item_key for char in ":.()[]{}|>"
                ):
                    node_key.style = '"'
                node.value.append((node_key, node_value))
            return node

    yaml_str = yaml.dump(
        yaml_output,
        Dumper=ModelNameDumper,
        default_flow_style=False,
        sort_keys=False,
        indent=2,
    )
    print(yaml_str)


def analyze_model(
    model_path: str,
    verbose: bool = False,
    yaml_output: bool = False,
    context_lengths: list[int] | None = None,
    skip_memory_test: bool = False,
    gpu_index: int = 0,
) -> dict[str, Any]:
    """
    Perform comprehensive analysis of a Hugging Face model.

    Args:
        model_path: Path to the HF model directory
        verbose: Whether to print detailed analysis
        yaml_output: Whether to generate standardized YAML output
        context_lengths: List of context lengths for profiles
        skip_memory_test: Whether to skip memory measurement
        gpu_index: GPU device index for memory testing

    Returns:
        Complete analysis dictionary
    """
    print(f"Analyzing Hugging Face model: {model_path}")

    # Get complete model info using the inspector
    info = get_model_info_summary(model_path)

    if yaml_output:
        # Generate and print standardized YAML output
        # use_profiles is True if context_lengths is provided
        use_profiles = context_lengths is not None and len(context_lengths) > 0
        yaml_result = generate_standardized_yaml(
            model_path, info, use_profiles, context_lengths, skip_memory_test, gpu_index
        )
        print_standardized_yaml(yaml_result)
        return yaml_result

    if verbose:
        # Print detailed analysis
        print_model_summary(info)

        capabilities = info.get("capabilities", {})
        if capabilities:
            print_capabilities_analysis(capabilities)

        chat_template = info.get("chat_template", {})
        if chat_template:
            print_chat_template_analysis(chat_template)

        validation = info.get("validation", {})
        if validation:
            print_validation_results(validation)

        # Print metadata if available
        metadata = info.get("metadata")
        if metadata:
            print_subsection_header("Full Metadata")
            for key, value in metadata.items():
                print_key_value(key, value)
    else:
        # Print concise summary
        print_model_summary(info)

    return info


def main():
    """Main entry point for the vLLM model analyzer."""
    parser = argparse.ArgumentParser(
        description="Analyze vLLM-compatible model directories (HF, GPTQ, AWQ) for capabilities, chat templates, and configuration",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s /path/to/model
  %(prog)s /path/to/model --verbose
  %(prog)s /path/to/model --json
  %(prog)s /path/to/model --yaml
  %(prog)s /path/to/model --output analysis.json
        """,
    )

    parser.add_argument(
        "model_path",
        nargs="?",
        default="/mnt/torus/models/Qwen3-8B",
        help="Path to the HF model directory (default: %(default)s)",
    )

    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Print detailed analysis information",
    )

    parser.add_argument(
        "--json", "-j", action="store_true", help="Output results in JSON format"
    )

    parser.add_argument(
        "--yaml",
        "-y",
        action="store_true",
        help="Output results in standardized YAML format (default)",
    )

    parser.add_argument("--output", "-o", help="Save results to specified file")

    parser.add_argument(
        "--quiet", "-q", action="store_true", help="Suppress all output except errors"
    )

    parser.add_argument(
        "--contexts",
        type=str,
        help="Comma-separated list of context lengths (e.g., 8192,16384,32768). If not specified, only max context will be used.",
    )

    parser.add_argument(
        "--gpu-index",
        type=int,
        default=0,
        help="GPU device index for memory testing (default: 0)",
    )

    parser.add_argument(
        "--gpu-memory-utilization",
        type=float,
        default=0.98,
        help="GPU memory utilization ratio for vLLM (0.0-1.0, default: 0.98)",
    )

    parser.add_argument(
        "--skip-memory-test",
        action="store_true",
        help="Skip memory measurement (leave resources as null)",
    )

    parser.add_argument(
        "--use-cached",
        action="store_true",
        help="Use the last cached configuration instead of generating new one",
    )

    parser.add_argument(
        "--push",
        action="store_true",
        help="Push configuration to universal-llm-gateway API",
    )

    parser.add_argument(
        "--api-url",
        default="http://localhost:9998",
        help="Universal-llm-gateway API URL (default: %(default)s)",
    )

    parser.add_argument(
        "--api-token", help="Management API token (or set MANAGEMENT_API_TOKEN env var)"
    )

    parser.add_argument(
        "--model-key", help="Model key for API push (defaults to openai_api_fields.id)"
    )

    parser.add_argument(
        "--update",
        action="store_true",
        help="Update existing model (use PUT instead of POST)",
    )

    args = parser.parse_args()

    # Check if model directory exists
    if not os.path.exists(args.model_path):
        print(f"Error: Model directory not found: {args.model_path}")
        sys.exit(1)

    # Validate GPU memory utilization
    if not (0.0 < args.gpu_memory_utilization <= 1.0):
        print(
            f"Error: GPU memory utilization must be between 0.0 and 1.0, got: {args.gpu_memory_utilization}"
        )
        sys.exit(1)

    try:
        # Get actual max_model_len from config (required)
        base_max_len = get_max_model_len_from_config(args.model_path)
        if base_max_len is None:
            print("Error: Could not determine max_model_len from model config.json")
            sys.exit(1)

        # Determine context lengths
        if args.contexts:
            context_lengths = [int(x.strip()) for x in args.contexts.split(",")]
            # Filter context lengths to only include values <= base_max_len
            original_count = len(context_lengths)
            context_lengths = [ctx for ctx in context_lengths if ctx <= base_max_len]
            if len(context_lengths) < original_count:
                filtered_out = original_count - len(context_lengths)
                logger.warning(
                    f"Filtered out {filtered_out} context length(s) that exceed model's max_model_len ({base_max_len})"
                )
            if not context_lengths:
                print(
                    f"Error: All specified context lengths exceed model's max_model_len ({base_max_len})"
                )
                sys.exit(1)
        else:
            # If no contexts specified, use the model's native max context length
            context_lengths = [base_max_len]

        # Check if user wants to use cached config
        results = None
        if args.use_cached:
            if not args.quiet:
                print("\n📋 Using cached configuration...")

            # Get API parameters for path lookup
            api_url = args.api_url
            api_token = args.api_token or os.environ.get("MANAGEMENT_API_TOKEN")

            results = get_latest_cached_config(
                args.model_path,
                context_lengths,
                api_url=api_url,
                api_token=api_token,
                model_key=args.model_key,
            )
            if results is None:
                print(
                    "❌ No cached configuration found. Run without --use-cached to generate new config."
                )
                sys.exit(1)

        # Generate new config if not using cache
        if results is None:
            # Perform analysis
            if not args.quiet:
                print("🔍 vLLM Model Analyzer", file=sys.stderr)
                print(f"📁 Analyzing: {args.model_path}", file=sys.stderr)
            print(
                f"📊 Generating profiles for context lengths: {context_lengths}",
                file=sys.stderr,
            )
            if not args.skip_memory_test:
                print(
                    "⚠️  Memory testing enabled - this may take several minutes per profile",
                    file=sys.stderr,
                )

            # Initialize schema_data
            schema_data = None

            # Fetch schema if API URL provided
            if args.api_url:
                try:
                    schema_data = fetch_schema_from_api(args.api_url)
                    if schema_data:
                        print("🔍 Schema validation enabled", file=sys.stderr)
                except Exception as e:
                    print(f"⚠️  Schema validation failed: {e}", file=sys.stderr)
                    print("   Continuing without schema validation...", file=sys.stderr)

            # Get model info
            info = get_model_info_summary(args.model_path)

            # Generate standardized YAML config by default (unless --verbose without --yaml/--json)
            # This matches gguf_model_config_generator.py behavior
            if args.verbose and not args.yaml and not args.json:
                # Verbose analysis mode (non-YAML output)
                results = analyze_model(
                    args.model_path,
                    verbose=args.verbose,
                    yaml_output=False,
                    context_lengths=context_lengths,
                    skip_memory_test=args.skip_memory_test,
                    gpu_index=args.gpu_index,
                )
            else:
                # Default: Generate YAML config (matches GGUF behavior)
                use_profiles = context_lengths is not None and len(context_lengths) > 0
                results = generate_standardized_yaml(
                    args.model_path,
                    info,
                    use_profiles,
                    context_lengths,
                    args.skip_memory_test,
                    args.gpu_index,
                    args.gpu_memory_utilization,
                )

            # Validate against schema if fetched
            if schema_data and not (args.verbose and not args.yaml and not args.json):
                try:
                    print(
                        "🔍 Validating configuration against API schema...",
                        file=sys.stderr,
                    )
                    validate_against_schema(results, schema_data)
                except Exception as e:
                    print(f"❌ Schema validation failed: {e}", file=sys.stderr)
                    if not args.quiet:
                        print("   Continuing anyway...", file=sys.stderr)

            # Save complete config to cache (only if it's a config, not verbose analysis)
            if not (args.verbose and not args.yaml and not args.json):
                try:
                    cache_key = compute_cache_key(args.model_path)
                    save_config_to_cache(cache_key, results)
                    if not args.quiet:
                        print(
                            "💾 Saved complete configuration to cache", file=sys.stderr
                        )
                except Exception as e:
                    logger.warning(f"Failed to save config to cache: {e}")

        # Push to API if requested
        if args.push:
            api_url = args.api_url
            api_token = args.api_token or os.environ.get("MANAGEMENT_API_TOKEN")

            if not results:
                print("\n❌ Error: No model configuration found")
                sys.exit(1)

            # Extract model config from gateway format
            models = results.get("models", {})
            if not models:
                print("\n❌ Error: No models found in configuration")
                sys.exit(1)

            # Get the first (and should be only) model config
            model_key, model_config = next(iter(models.items()))
            model_key = args.model_key or model_key

            if not model_key:
                print(
                    "\n❌ Error: No model key specified. Use --model-key or check configuration"
                )
                sys.exit(1)

            # Push the model config (not the full gateway format)
            success = push_to_api(
                model_config, model_key, api_url, api_token, update=args.update
            )
            if not success:
                sys.exit(1)

        # Output configuration (YAML by default, unless --verbose mode without --yaml/--json)
        if not (args.verbose and not args.yaml and not args.json):
            # Print summary to stderr (similar to GGUF)
            if not args.quiet:
                print_config_summary(results)

            # Determine output format (YAML by default, JSON if --json specified)
            output_format = "json" if args.json else "yaml"

            if output_format == "json":
                output_str = json.dumps(results, indent=2, default=str)
            else:  # yaml
                output_str = yaml.dump(
                    results, default_flow_style=False, sort_keys=False, indent=2
                )

            if args.output:
                with open(args.output, "w") as f:
                    f.write(output_str)
                print(f"\n✅ Configuration written to: {args.output}", file=sys.stderr)
            else:
                # Output to stdout (not stderr)
                print(output_str)

            if not args.quiet:
                print("\n✅ Configuration generation complete!", file=sys.stderr)
        else:
            # Verbose analysis mode
            if not args.quiet:
                print("\n✅ Analysis complete!", file=sys.stderr)

    except KeyboardInterrupt:
        print("\n\n⚠️  Analysis interrupted by user")
        sys.exit(1)
    except Exception as e:
        print(f"\n❌ Error during analysis: {e}")
        if args.verbose:
            import traceback

            traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
