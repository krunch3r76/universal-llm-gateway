"""
vLLM model inspector and analysis utilities.

Provides functions to analyze HuggingFace models for vLLM compatibility:
- Model format detection (safetensors, pytorch, etc.)
- Architecture compatibility analysis
- Configuration recommendations
- Tokenizer analysis
"""

import importlib.util
import os
from typing import Any

from universal_logging import get_logger

logger = get_logger(__name__)

try:
    from transformers import AutoConfig, AutoTokenizer

    transformers_available = True
except ImportError:
    transformers_available = False
    logger.warning("Transformers not available - limited inspection capabilities")

safetensors_available = importlib.util.find_spec("safetensors") is not None


def inspect_model_path(model_path: str) -> dict[str, Any]:
    """
    Inspect a model path to determine if it's compatible with vLLM.

    Args:
        model_path: Path to the model directory or file

    Returns:
        Dictionary with inspection results
    """
    result = {
        "path": model_path,
        "exists": False,
        "is_directory": False,
        "format": None,
        "architecture": None,
        "has_config": False,
        "has_tokenizer": False,
        "has_safetensors": False,
        "has_pytorch": False,
        "compatible": False,
        "recommendations": [],
    }

    if not os.path.exists(model_path):
        result["recommendations"].append("Model path does not exist")
        return result

    result["exists"] = True
    result["is_directory"] = os.path.isdir(model_path)

    if result["is_directory"]:
        # Analyze directory structure
        files = os.listdir(model_path)

        # Check for config files
        config_files = [
            f for f in files if f in ["config.json", "config.yaml", "config.yml"]
        ]
        result["has_config"] = len(config_files) > 0

        # Check for tokenizer files
        tokenizer_files = [
            f
            for f in files
            if f.startswith("tokenizer")
            or f in ["vocab.json", "merges.txt", "special_tokens_map.json"]
        ]
        result["has_tokenizer"] = len(tokenizer_files) > 0

        # Check for model weight files
        safetensors_files = [f for f in files if f.endswith(".safetensors")]
        pytorch_files = [f for f in files if f.endswith((".bin", ".pt", ".pth"))]

        result["has_safetensors"] = len(safetensors_files) > 0
        result["has_pytorch"] = len(pytorch_files) > 0

        # Determine format
        if result["has_safetensors"]:
            result["format"] = "safetensors"
        elif result["has_pytorch"]:
            result["format"] = "pytorch"
        else:
            result["format"] = "unknown"

        # Try to get architecture from config
        if result["has_config"] and transformers_available:
            try:
                config = AutoConfig.from_pretrained(model_path, trust_remote_code=True)
                result["architecture"] = getattr(config, "model_type", None)
            except Exception as e:
                logger.debug(f"Could not load config: {e}")

        # Check compatibility
        result["compatible"] = (
            result["has_config"]
            and result["has_tokenizer"]
            and (result["has_safetensors"] or result["has_pytorch"])
        )

        # Generate recommendations
        if not result["has_config"]:
            result["recommendations"].append(
                "Missing config.json - model may not be HuggingFace format"
            )
        if not result["has_tokenizer"]:
            result["recommendations"].append(
                "Missing tokenizer files - tokenization may not work"
            )
        if not result["has_safetensors"] and not result["has_pytorch"]:
            result["recommendations"].append("No model weight files found")

        if result["compatible"]:
            result["recommendations"].append("Model appears compatible with vLLM")

    else:
        # Single file - check if it's a supported format
        if model_path.endswith(".safetensors"):
            result["format"] = "safetensors"
            result["has_safetensors"] = True
            result["recommendations"].append(
                "Single safetensors file - may need config.json in same directory"
            )
        elif model_path.endswith((".bin", ".pt", ".pth")):
            result["format"] = "pytorch"
            result["has_pytorch"] = True
            result["recommendations"].append(
                "Single pytorch file - may need config.json in same directory"
            )
        else:
            result["format"] = "unknown"
            result["recommendations"].append(
                "Unknown file format - not compatible with vLLM"
            )

    return result


def get_model_info(model_path: str) -> dict[str, Any]:
    """
    Get detailed model information for vLLM compatibility.

    Args:
        model_path: Path to the model directory

    Returns:
        Dictionary with detailed model information
    """
    info = {
        "path": model_path,
        "inspection": inspect_model_path(model_path),
        "config": None,
        "tokenizer_info": None,
        "model_info": {},  # Architecture info only, not loader defaults
    }

    if not transformers_available:
        info["error"] = "Transformers not available - cannot get detailed info"
        return info

    try:
        # Load config
        config = AutoConfig.from_pretrained(model_path, trust_remote_code=True)
        info["config"] = {
            "model_type": getattr(config, "model_type", None),
            "architectures": getattr(config, "architectures", None),
            "vocab_size": getattr(config, "vocab_size", None),
            "hidden_size": getattr(config, "hidden_size", None),
            "num_attention_heads": getattr(config, "num_attention_heads", None),
            "num_hidden_layers": getattr(config, "num_hidden_layers", None),
            "max_position_embeddings": getattr(config, "max_position_embeddings", None),
        }

        # Load tokenizer info
        try:
            tokenizer = AutoTokenizer.from_pretrained(
                model_path, trust_remote_code=True
            )
            info["tokenizer_info"] = {
                "vocab_size": getattr(tokenizer, "vocab_size", None),
                "model_max_length": getattr(tokenizer, "model_max_length", None),
                "has_chat_template": hasattr(tokenizer, "apply_chat_template")
                and bool(tokenizer.chat_template),
                "pad_token": getattr(tokenizer, "pad_token", None),
                "eos_token": getattr(tokenizer, "eos_token", None),
            }
        except Exception as e:
            info["tokenizer_error"] = str(e)

        # Extract model architecture information (not loader defaults)
        if info["config"]["model_type"]:
            info["model_info"] = _get_model_architecture_info(info["config"])

    except Exception as e:
        info["error"] = f"Failed to load model info: {e}"

    return info


def _get_model_architecture_info(config: Any) -> dict[str, Any]:
    """
    Extract model architecture information from config.

    Returns architecture details only - does NOT provide loader configuration.
    This is for inspection/analysis, not for setting defaults.
    """
    model_type = getattr(config, "model_type", None)
    max_length = getattr(config, "max_position_embeddings", 2048)

    settings = {
        "model_type": model_type,
        "max_position_embeddings": max_length,
        "architecture_notes": [],
    }

    # Model-specific notes
    if model_type in ["gemma", "gemma2"]:
        settings["architecture_notes"].append("Gemma2 models typically use bfloat16")
    elif model_type in ["llama", "mistral", "qwen"]:
        settings["architecture_notes"].append("Standard transformer architecture")
    else:
        settings["architecture_notes"].append(f"Model type: {model_type}")

    return settings


def analyze_model_compatibility(model_path: str) -> dict[str, Any]:
    """
    Comprehensive analysis of model compatibility with vLLM.

    Args:
        model_path: Path to the model directory

    Returns:
        Comprehensive compatibility analysis
    """
    analysis = {
        "model_path": model_path,
        "inspection": inspect_model_path(model_path),
        "detailed_info": get_model_info(model_path),
        "compatibility_score": 0,
        "issues": [],
        "recommendations": [],
    }

    # Calculate compatibility score
    inspection = analysis["inspection"]
    score = 0

    if inspection["exists"]:
        score += 20
    if inspection["has_config"]:
        score += 30
    if inspection["has_tokenizer"]:
        score += 30
    if inspection["has_safetensors"] or inspection["has_pytorch"]:
        score += 20

    analysis["compatibility_score"] = score

    # Generate issues and recommendations
    if score < 50:
        analysis["issues"].append("Model has significant compatibility issues")
        analysis["recommendations"].append(
            "Consider using a different model format or fixing missing files"
        )
    elif score < 80:
        analysis["issues"].append("Model has minor compatibility issues")
        analysis["recommendations"].append("Model should work but may have limitations")
    else:
        analysis["recommendations"].append("Model appears fully compatible with vLLM")

    # Add architecture information if available
    if analysis["detailed_info"].get("model_info"):
        settings = analysis["detailed_info"]["model_info"]
        if "architecture_notes" in settings:
            analysis["recommendations"].extend(settings["architecture_notes"])

    return analysis


def get_vllm_model_info(model_path: str) -> dict[str, Any]:
    """
    Get complete model information summary for vLLM model.

    Args:
        model_path: Path to the model directory

    Returns:
        Complete model information dictionary
    """
    return {
        "model_path": model_path,
        "inspection": inspect_model_path(model_path),
        "detailed_info": get_model_info(model_path),
        "compatibility_analysis": analyze_model_compatibility(model_path),
        "model_info": _get_model_architecture_info_from_path(model_path),
        "capabilities": _get_model_capabilities(model_path),
        "validation": _validate_vllm_requirements(model_path),
    }


def _get_model_architecture_info_from_path(model_path: str) -> dict[str, Any]:
    """
    Extract model architecture information from model path.

    Returns architecture details only - does NOT provide loader configuration.
    """
    info = get_model_info(model_path)
    if info.get("config"):
        return _get_model_architecture_info(info["config"])
    else:
        # No config available - return minimal info
        return {
            "model_type": "unknown",
            "max_position_embeddings": "unknown",
            "architecture_notes": ["No config.json found"],
        }


def _get_model_capabilities(model_path: str) -> dict[str, Any]:
    """Get model capabilities analysis."""
    capabilities = {
        "supports_chat": False,
        "supports_chatml": False,
        "supports_streaming": True,
        "supports_batch_processing": True,
        "max_context_length": 2048,
        "recommended_use_cases": [],
    }

    try:
        if transformers_available:
            tokenizer = AutoTokenizer.from_pretrained(
                model_path, trust_remote_code=True
            )
            capabilities["supports_chat"] = hasattr(
                tokenizer, "apply_chat_template"
            ) and bool(tokenizer.chat_template)
            capabilities["supports_chatml"] = capabilities["supports_chat"]

            config = AutoConfig.from_pretrained(model_path, trust_remote_code=True)
            max_length = getattr(config, "max_position_embeddings", 2048)
            capabilities["max_context_length"] = min(max_length, 8192)

            # Determine use cases based on model type
            model_type = getattr(config, "model_type", "unknown")
            if model_type in ["gemma", "gemma2"]:
                capabilities["recommended_use_cases"] = [
                    "creative_writing",
                    "story_generation",
                    "general_chat",
                ]
            elif model_type in ["llama", "mistral"]:
                capabilities["recommended_use_cases"] = [
                    "general_chat",
                    "instruction_following",
                    "code_generation",
                ]
            elif model_type in ["qwen"]:
                capabilities["recommended_use_cases"] = [
                    "code_generation",
                    "mathematical_reasoning",
                    "general_chat",
                ]
            else:
                capabilities["recommended_use_cases"] = [
                    "general_chat",
                    "text_generation",
                ]

    except Exception as e:
        logger.debug(f"Could not analyze capabilities: {e}")

    return capabilities


def _validate_vllm_requirements(model_path: str) -> dict[str, Any]:
    """Validate that model meets vLLM requirements."""
    validation = {
        "path_exists": False,
        "is_directory": False,
        "has_config": False,
        "has_tokenizer": False,
        "has_weights": False,
        "compatible_format": False,
        "requirements_met": False,
        "issues": [],
        "warnings": [],
    }

    inspection = inspect_model_path(model_path)

    validation["path_exists"] = inspection["exists"]
    validation["is_directory"] = inspection["is_directory"]
    validation["has_config"] = inspection["has_config"]
    validation["has_tokenizer"] = inspection["has_tokenizer"]
    validation["has_weights"] = (
        inspection["has_safetensors"] or inspection["has_pytorch"]
    )
    validation["compatible_format"] = inspection["format"] in ["safetensors", "pytorch"]

    # Check requirements
    if not validation["path_exists"]:
        validation["issues"].append("Model path does not exist")
    if not validation["is_directory"]:
        validation["issues"].append("vLLM requires model directory, not single file")
    if not validation["has_config"]:
        validation["issues"].append(
            "Missing config.json - required for HuggingFace models"
        )
    if not validation["has_tokenizer"]:
        validation["issues"].append(
            "Missing tokenizer files - required for text processing"
        )
    if not validation["has_weights"]:
        validation["issues"].append("No model weight files found")
    if not validation["compatible_format"]:
        validation["issues"].append(f"Unsupported format: {inspection['format']}")

    # Check for warnings
    if inspection["format"] == "pytorch" and not inspection["has_safetensors"]:
        validation["warnings"].append(
            "Using PyTorch format - safetensors is preferred for safety"
        )

    # Overall assessment
    validation["requirements_met"] = (
        validation["path_exists"]
        and validation["is_directory"]
        and validation["has_config"]
        and validation["has_tokenizer"]
        and validation["has_weights"]
        and validation["compatible_format"]
    )

    return validation
