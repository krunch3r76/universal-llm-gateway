"""
ExLlamaV3 model inspector for GPTQ models.

Provides model analysis capabilities optimized for ExLlamaV3 inference.
Enhanced compatibility detection and EXL3 format support.
"""

import json
from pathlib import Path
from typing import Any

try:
    from exllamav3 import Config

    exllamav3_available = True
except ImportError:
    exllamav3_available = False

from ..gptq.inspector import get_model_info_summary


def get_exllamav3_model_info(model_path: str) -> dict[str, Any]:
    """Get ExLlamaV3-specific model information"""
    if not exllamav3_available:
        return {
            "error": "ExLlamaV3 not available",
            "exllamav3_compatible": False,
            "install_hint": "Run: ./libs/inference_djinn/scripts/build/shell/exlamma/build_exllamav3_blackwell.sh",
        }

    try:
        # Get base model info
        base_info = get_model_info_summary(model_path)

        # Add ExLlamaV3-specific analysis
        exllamav3_info = _analyze_exllamav3_compatibility(model_path)

        return {**base_info, **exllamav3_info}

    except Exception as e:
        return {
            "error": f"Failed to analyze ExLlamaV3 model: {e}",
            "exllamav3_compatible": False,
        }


def _analyze_exllamav3_compatibility(model_path: str) -> dict[str, Any]:
    """Analyze model compatibility with ExLlamaV3"""
    model_dir = Path(model_path)

    # Check for required files
    required_files = ["config.json", "tokenizer.json", "tokenizer_config.json"]
    missing_files = []

    for file in required_files:
        if not (model_dir / file).exists():
            missing_files.append(file)

    # Check for model files (both standard and EXL3 formats)
    model_files = list(model_dir.glob("*.safetensors")) + list(model_dir.glob("*.bin"))
    exl3_files = list(model_dir.glob("*.exl3"))

    # Check for quantization info
    quantization_info = _detect_quantization(model_dir)

    # Check for EXL3 format
    exl3_info = _analyze_exl3_format(model_dir)

    # Estimate memory requirements
    memory_estimate = _estimate_memory_requirements(
        model_dir, quantization_info, exl3_info
    )

    # Enhanced compatibility check
    is_compatible = _check_exllamav3_compatibility(
        model_dir, quantization_info, exl3_info
    )

    return {
        "exllamav3_compatible": is_compatible,
        "missing_files": missing_files,
        "model_files": [str(f) for f in model_files],
        "exl3_files": [str(f) for f in exl3_files],
        "quantization": quantization_info,
        "exl3_format": exl3_info,
        "memory_estimate": memory_estimate,
        "recommended_config": _get_recommended_config(
            model_dir, quantization_info, exl3_info
        ),
        "compatibility_notes": _get_compatibility_notes(quantization_info, exl3_info),
    }


def _check_exllamav3_compatibility(
    model_dir: Path, quantization_info: dict[str, Any], exl3_info: dict[str, Any]
) -> bool:
    """Check if model is compatible with ExLlamaV3"""
    # Basic file requirements
    required_files = ["config.json", "tokenizer.json", "tokenizer_config.json"]
    for file in required_files:
        if not (model_dir / file).exists():
            return False

    # Model files requirement
    model_files = list(model_dir.glob("*.safetensors")) + list(model_dir.glob("*.bin"))
    exl3_files = list(model_dir.glob("*.exl3"))

    if len(model_files) == 0 and len(exl3_files) == 0:
        return False

    # ExLlamaV3 has better compatibility than ExLlamaV2
    # It should handle most standard GPTQ models
    return True


def _analyze_exl3_format(model_dir: Path) -> dict[str, Any]:
    """Analyze EXL3 format specific information"""
    exl3_files = list(model_dir.glob("*.exl3"))

    exl3_info = {
        "present": len(exl3_files) > 0,
        "files": [str(f) for f in exl3_files],
        "total_size_mb": 0,
        "compression_ratio": None,
    }

    if exl3_files:
        # Calculate total size
        total_size = sum(f.stat().st_size for f in exl3_files)
        exl3_info["total_size_mb"] = total_size / (1024 * 1024)

        # Check for original model size to calculate compression ratio
        original_files = list(model_dir.glob("*.safetensors")) + list(
            model_dir.glob("*.bin")
        )
        if original_files:
            original_size = sum(f.stat().st_size for f in original_files)
            if original_size > 0:
                exl3_info["compression_ratio"] = total_size / original_size

    return exl3_info


def _detect_quantization(model_dir: Path) -> dict[str, Any]:
    """Detect quantization information from model files"""
    quantization_info = {
        "type": "unknown",
        "bits": None,
        "group_size": None,
        "desc_act": None,
        "format": "unknown",
    }

    # Check config.json for quantization info
    config_file = model_dir / "config.json"
    if config_file.exists():
        try:
            with open(config_file) as f:
                config = json.load(f)

            # Check for quantization config
            if "quantization_config" in config:
                quant_config = config["quantization_config"]
                quantization_info.update(
                    {
                        "type": quant_config.get("quant_method", "unknown"),
                        "bits": quant_config.get("bits", None),
                        "group_size": quant_config.get("group_size", None),
                        "desc_act": quant_config.get("desc_act", None),
                        "format": "gptq",
                    }
                )

            # Check for other quantization indicators
            if "torch_dtype" in config:
                dtype = config["torch_dtype"]
                if "int" in str(dtype).lower():
                    if quantization_info["type"] == "unknown":
                        quantization_info["type"] = "int_quantization"

        except Exception as e:
            print(f"Warning: Could not parse config.json: {e}")

    # Check for EXL3 files
    exl3_files = list(model_dir.glob("*.exl3"))
    if exl3_files:
        quantization_info["format"] = "exl3"
        if quantization_info["type"] == "unknown":
            quantization_info["type"] = "exl3"

    return quantization_info


def _estimate_memory_requirements(
    model_dir: Path, quantization_info: dict[str, Any], exl3_info: dict[str, Any]
) -> dict[str, Any]:
    """Estimate memory requirements for ExLlamaV3"""

    # Calculate model size
    model_files = list(model_dir.glob("*.safetensors")) + list(model_dir.glob("*.bin"))
    exl3_files = list(model_dir.glob("*.exl3"))

    if exl3_files:
        # Use EXL3 files for size calculation
        model_size_mb = sum(f.stat().st_size for f in exl3_files) / (1024 * 1024)
    elif model_files:
        model_size_mb = sum(f.stat().st_size for f in model_files) / (1024 * 1024)
    else:
        model_size_mb = 0

    # ExLlamaV3 memory estimation (improved over ExLlamaV2)
    base_memory_mb = model_size_mb

    # Overhead for ExLlamaV3 (generally lower than ExLlamaV2)
    overhead_factor = 1.2  # 20% overhead (improved from ExLlamaV2's ~30%)

    # Context memory (scales with sequence length)
    context_memory_mb = 512  # Base context memory

    # Adjust based on quantization
    if quantization_info.get("format") == "exl3":
        # EXL3 format is more memory efficient
        overhead_factor = 1.1
    elif quantization_info.get("bits"):
        bits = quantization_info["bits"]
        if bits <= 4:
            overhead_factor = 1.15
        elif bits <= 8:
            overhead_factor = 1.25

    total_memory_mb = (base_memory_mb * overhead_factor) + context_memory_mb

    return {
        "model_size_mb": model_size_mb,
        "estimated_memory_mb": total_memory_mb,
        "overhead_factor": overhead_factor,
        "context_memory_mb": context_memory_mb,
        "format_efficiency": "high" if exl3_info["present"] else "standard",
    }


def _get_recommended_config(
    model_dir: Path, quantization_info: dict[str, Any], exl3_info: dict[str, Any]
) -> dict[str, Any]:
    """Get recommended configuration for ExLlamaV3"""
    config = {
        "max_seq_len": 2048,
        "max_input_len": 2048,
        "chunk_size": 2048,
        "progress": True,
    }

    # Adjust based on model size
    model_size_mb = _estimate_memory_requirements(
        model_dir, quantization_info, exl3_info
    )["model_size_mb"]

    if model_size_mb > 40000:  # >40GB models
        config.update({"max_seq_len": 4096, "chunk_size": 1024, "lazy": True})
    elif model_size_mb > 20000:  # >20GB models
        config.update({"max_seq_len": 4096, "chunk_size": 2048})

    # EXL3 optimizations
    if exl3_info["present"]:
        config.update(
            {
                "max_seq_len": 8192,  # EXL3 can handle longer sequences
                "chunk_size": 4096,
            }
        )

    # Quantization-specific adjustments
    if quantization_info.get("bits"):
        bits = quantization_info["bits"]
        if bits <= 4:
            config["chunk_size"] = min(config["chunk_size"], 2048)

    return config


def _get_compatibility_notes(
    quantization_info: dict[str, Any], exl3_info: dict[str, Any]
) -> list[str]:
    """Get compatibility notes for ExLlamaV3"""
    notes = []

    if exl3_info["present"]:
        notes.append("✅ EXL3 format detected - optimal for ExLlamaV3")
        notes.append("✅ Enhanced compression and faster loading expected")
    else:
        notes.append(
            "📄 Standard GPTQ format - will work but consider converting to EXL3"
        )

    if quantization_info.get("type") == "gptq":
        notes.append(
            "✅ GPTQ quantization detected - ExLlamaV3 has improved GPTQ support"
        )
        notes.append("✅ No embedding.embedding=None errors expected")

    if quantization_info.get("bits"):
        bits = quantization_info["bits"]
        if bits <= 4:
            notes.append(
                f"⚡ {bits}-bit quantization - excellent speed/quality balance"
            )
        elif bits <= 8:
            notes.append(
                f"🎯 {bits}-bit quantization - good quality with reasonable speed"
            )

    notes.append(
        "✅ ExLlamaV3 features: Better architecture support, improved device handling"
    )
    notes.append("✅ Modern model compatibility improvements over ExLlamaV2")

    return notes


def benchmark_exllamav3_performance(model_path: str) -> dict[str, Any]:
    """Benchmark ExLlamaV3 performance (placeholder for future implementation)"""
    return {
        "note": "Performance benchmarking not yet implemented for ExLlamaV3",
        "expected_improvements": [
            "Better architecture support",
            "Improved device handling",
            "EXL3 format optimizations",
            "Reduced memory usage",
            "Faster model loading",
        ],
    }


def convert_to_exl3_recommendation(model_path: str) -> dict[str, Any]:
    """Provide recommendations for converting to EXL3 format"""
    model_dir = Path(model_path)
    exl3_files = list(model_dir.glob("*.exl3"))

    if exl3_files:
        return {"already_exl3": True, "message": "Model is already in EXL3 format"}

    # Check if conversion is recommended
    model_files = list(model_dir.glob("*.safetensors")) + list(model_dir.glob("*.bin"))
    if not model_files:
        return {
            "can_convert": False,
            "message": "No compatible model files found for conversion",
        }

    model_size_mb = sum(f.stat().st_size for f in model_files) / (1024 * 1024)

    return {
        "can_convert": True,
        "recommended": model_size_mb > 5000,  # Recommend for models >5GB
        "model_size_mb": model_size_mb,
        "expected_benefits": [
            "Faster loading times",
            "Better compression",
            "Improved inference speed",
            "Enhanced ExLlamaV3 compatibility",
        ],
        "conversion_command": f"python exllamav3/convert.py -i {model_path} -o {model_path}_exl3 -w /tmp/convert_work -b 4.0",
        "note": "Conversion requires significant temporary disk space",
    }
