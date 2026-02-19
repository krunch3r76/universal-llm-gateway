#!/usr/bin/env python3
"""
vLLM Memory Test - Tests vLLM model loading and measures memory usage

This script tests if a vLLM model can be loaded successfully with specific parameters.
It exits with code 0 for success, non-zero for failure.
Outputs resource usage as JSON on stderr for the parent to parse, keeping stdout clean.

Optimized for RTX 5090 (SM_120 / Blackwell architecture):
- Uses enforce_eager=True to bypass Torch Inductor compilation errors
- Sets optimal environment variables for SM_120
- Disables Torch Dynamo to prevent symbol codegen issues
- Uses Flash Attention backend (not Triton version)

See: diagnostics/vllm/TORCH_INDUCTOR_TROUBLESHOOTING.md for details
"""

import argparse
import gc
import json
import logging
import os
import sys
from pathlib import Path

# CRITICAL: Set vLLM engine version BEFORE any vLLM imports
# Try V1 engine since V0 has compatibility issues with this vLLM version
os.environ["VLLM_USE_V1"] = "1"

# Set optimal environment for RTX 5090 BEFORE vLLM imports
os.environ["TORCH_CUDA_ARCH_LIST"] = "12.0"
# Force vLLM to use Flash Attention 2 (not 3) for RTX 5090 compatibility
os.environ["VLLM_ATTENTION_BACKEND"] = "FLASH_ATTN"
os.environ["VLLM_FLASH_ATTN_VERSION"] = (
    "2"  # Use FA2 for Blackwell/RTX 5090 compatibility
)
os.environ["VLLM_USE_TRITON_FLASH_ATTN"] = "0"

# PERFORMANCE: Disable Inductor autotuning by default to prevent first-inference slowdown
# These flags default to "1" in vLLM 0.11+ and trigger coordinate descent tuning
# and max_autotune on first inference, which can add 30-60+ seconds of delay.
# Using setdefault() so users can override by setting these env vars before import.
os.environ.setdefault("VLLM_ENABLE_INDUCTOR_MAX_AUTOTUNE", "0")
os.environ.setdefault("VLLM_ENABLE_INDUCTOR_COORDINATE_DESCENT_TUNING", "0")

# Set PyTorch CUDA allocator to use expandable segments to reduce fragmentation
os.environ["PYTORCH_CUDA_ALLOC_CONF"] = "expandable_segments:True"

# Suppress most vLLM logging to keep output clean
logging.basicConfig(level=logging.WARNING)
os.environ["VLLM_LOGGING_LEVEL"] = "WARNING"


def detect_quantization_and_rope_scaling(model_path: str) -> tuple[str | None, bool]:
    """
    Detect quantization format and whether the model uses RoPE scaling.

    Returns:
        Tuple of (quantization_format, has_rope_scaling) where:
        - quantization_format: "awq", "gptq", or None
        - has_rope_scaling: True if model uses RoPE scaling
    """
    config_path = os.path.join(model_path, "config.json")

    if not os.path.exists(config_path):
        return None, False

    try:
        with open(config_path) as f:
            config = json.load(f)

        # Detect quantization format
        quantization = None
        quant_config = config.get("quantization_config")
        if quant_config:
            quant_method = quant_config.get("quant_method", "").lower()
            if quant_method == "awq" or quant_config.get("version") == "awq":
                quantization = "awq"
            elif quant_method == "gptq":
                quantization = "gptq"

        # Check for RoPE scaling
        has_rope_scaling = False
        rope_scaling = config.get("rope_scaling")
        if rope_scaling and isinstance(rope_scaling, dict):
            # RoPE scaling is present if it's a non-empty dict
            has_rope_scaling = True

        return quantization, has_rope_scaling

    except (OSError, json.JSONDecodeError) as e:
        print(
            f"[MEMORY_TEST] Warning: Could not parse config.json: {e}", file=sys.stderr
        )
        return None, False


def get_gpu_memory_info(device_index: int = 0) -> dict:
    """
    Get comprehensive GPU memory information using nvidia-ml-py3.
    Returns dict with total, used, free memory in MB, and process-specific usage.
    """
    try:
        import pynvml as nvml

        nvml.nvmlInit()
        handle = nvml.nvmlDeviceGetHandleByIndex(device_index)

        # Get total GPU memory info
        meminfo = nvml.nvmlDeviceGetMemoryInfo(handle)
        total_mb = meminfo.total // (1024 * 1024)
        used_mb = meminfo.used // (1024 * 1024)
        free_mb = meminfo.free // (1024 * 1024)

        # Get process-specific memory usage
        process_vram_mb = 0
        try:
            processes = nvml.nvmlDeviceGetComputeRunningProcesses(handle)
            my_pid = os.getpid()
            for proc in processes:
                if proc.pid == my_pid:
                    process_vram_mb = proc.usedGpuMemory // (1024 * 1024)
                    break
        except Exception:
            pass  # If process enumeration fails, use total usage

        nvml.nvmlShutdown()

        return {
            "total_mb": int(total_mb),
            "used_mb": int(used_mb),
            "free_mb": int(free_mb),
            "process_mb": int(process_vram_mb),
        }

    except ImportError:
        raise ImportError(
            "nvidia-ml-py3 not available. Install with: pip install nvidia-ml-py3"
        )
    except Exception as e:
        raise RuntimeError(f"nvidia-ml-py3 failed: {e}")


def get_process_rss_mb() -> int:
    """
    Return current process RSS (Resident Set Size) in MB.
    This is the actual physical RAM used by the process.
    """
    try:
        import psutil

        process = psutil.Process(os.getpid())
        # Use rss (resident set size) - actual physical memory used
        return process.memory_info().rss // (1024 * 1024)
    except ImportError:
        # Fallback to /proc/self/status (Linux only)
        try:
            with open("/proc/self/status") as f:
                for line in f:
                    if line.startswith("VmRSS:"):
                        # VmRSS is in kB
                        return int(line.split()[1]) // 1024
        except Exception as e:
            raise RuntimeError(f"Failed to get process memory info: {e}")
    except Exception as e:
        raise RuntimeError(f"psutil failed: {e}")


def test_vllm_load(
    model_path: str,
    max_model_len: int,
    quantization: str | None,
    gpu_memory_utilization: float,
) -> dict:
    """
    Test if model loads successfully with vLLM.
    Returns dict with success status and comprehensive resource usage.
    """
    result = {"success": False, "ram_mb": None, "vram_mb": None, "error": None}

    try:
        # Detect quantization format and RoPE scaling before loading
        detected_quantization, has_rope_scaling = detect_quantization_and_rope_scaling(
            model_path
        )

        # Use detected quantization if not provided
        if quantization is None:
            quantization = detected_quantization

        print(f"[MEMORY_TEST] Detected quantization: {quantization}", file=sys.stderr)
        print(f"[MEMORY_TEST] Has RoPE scaling: {has_rope_scaling}", file=sys.stderr)

        # Get baseline GPU memory info
        gpu_before = get_gpu_memory_info(device_index=0)
        ram_before = get_process_rss_mb()
        print(
            f"[MEMORY_TEST] Requested GPU memory utilization: {gpu_memory_utilization}",
            file=sys.stderr,
        )
        print(
            f"[MEMORY_TEST] Requested max model length: {max_model_len}",
            file=sys.stderr,
        )
        print(
            f"[MEMORY_TEST] Starting test for {model_path} with max_model_len={max_model_len}",
            file=sys.stderr,
        )
        print(
            f"[MEMORY_TEST] Baseline GPU usage: {gpu_before['used_mb']} MB",
            file=sys.stderr,
        )
        print(f"[MEMORY_TEST] Baseline RAM usage: {ram_before} MB", file=sys.stderr)

        # Disable Torch compilation to avoid Inductor errors
        import torch

        torch._dynamo.config.disable = True

        try:
            from vllm import LLM

            print("[MEMORY_TEST] Imported vLLM successfully", file=sys.stderr)

            # Force V1 engine by setting environment variable again (in case it was overridden)
            os.environ["VLLM_USE_V1"] = "1"

            # Determine if we can safely disable sliding window
            # AWQ models with RoPE scaling cannot have sliding window disabled
            can_disable_sliding_window = not (
                quantization == "awq" and has_rope_scaling
            )

            print(
                f"[MEMORY_TEST] Can disable sliding window: {can_disable_sliding_window}",
                file=sys.stderr,
            )
            if not can_disable_sliding_window:
                print(
                    "[MEMORY_TEST] Keeping sliding window enabled for AWQ model with RoPE scaling",
                    file=sys.stderr,
                )

            # Build vLLM parameters optimized for RTX 5090
            vllm_params = {
                "model": model_path,
                "max_model_len": max_model_len,
                "gpu_memory_utilization": gpu_memory_utilization,
                "enforce_eager": True,  # KEY FIX: Prevents Inductor errors
                "disable_custom_all_reduce": True,
                "disable_log_stats": True,
                "trust_remote_code": False,
                # GPTQ requires float16, not bfloat16
                "dtype": "float16" if quantization == "gptq" else "auto",
                # Conditionally disable sliding window based on model compatibility
                "disable_sliding_window": can_disable_sliding_window,
                "max_num_batched_tokens": max_model_len,  # Set to max_model_len to avoid dynamic sizing
            }

            print(
                f"[MEMORY_TEST] Using GPU memory utilization: {gpu_memory_utilization}",
                file=sys.stderr,
            )

            print("[MEMORY_TEST] Loading vLLM model...", file=sys.stderr)

            # Redirect fd 1 → fd 2 during vLLM load: vLLM V1 forks an
            # EngineCore subprocess that inherits OS file descriptors.
            # Its stdout writes bypass Python's sys.stdout, so we must
            # redirect at the fd level to keep our stdout clean for JSON.
            sys.stdout.flush()
            saved_fd = os.dup(1)
            os.dup2(2, 1)
            try:
                llm = LLM(**vllm_params)
            finally:
                sys.stdout.flush()
                os.dup2(saved_fd, 1)
                os.close(saved_fd)

            print("[MEMORY_TEST] Model loaded successfully", file=sys.stderr)

            # Measure resources after load
            gpu_after = get_gpu_memory_info(device_index=0)
            ram_after = get_process_rss_mb()

            # Calculate deltas
            ram_delta = max(0, ram_after - ram_before)
            vram_delta = max(0, gpu_after["used_mb"] - gpu_before["used_mb"])

            # Use the larger of process-specific or total GPU delta
            vram_mb = max(gpu_after["process_mb"], vram_delta)

            print(
                f"[MEMORY_TEST] Final GPU usage: {gpu_after['used_mb']} MB (delta: {vram_delta} MB)",
                file=sys.stderr,
            )
            print(
                f"[MEMORY_TEST] Final RAM usage: {ram_after} MB (delta: {ram_delta} MB)",
                file=sys.stderr,
            )
            print(
                f"[MEMORY_TEST] Process VRAM: {gpu_after['process_mb']} MB",
                file=sys.stderr,
            )

            result.update(
                {
                    "success": True,
                    "ram_mb": ram_delta,
                    "vram_mb": vram_mb,
                    "gpu_total_used_mb": gpu_after["used_mb"],
                    "gpu_process_mb": gpu_after["process_mb"],
                    "gpu_delta_mb": vram_delta,
                    "baseline_gpu_mb": gpu_before["used_mb"],
                    "baseline_ram_mb": ram_before,
                }
            )

            # Aggressive cleanup to free GPU memory
            try:
                del llm
            except Exception:
                pass

            # Clear Python garbage collection
            gc.collect()

            # Clear PyTorch CUDA cache (critical for freeing GPU memory)
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
                torch.cuda.synchronize()  # Wait for all operations to complete
                print("[MEMORY_TEST] Cleared CUDA cache", file=sys.stderr)

            # Force garbage collection again after CUDA cleanup
            gc.collect()

            print("[MEMORY_TEST] Aggressive cleanup completed", file=sys.stderr)

            return result

        except Exception as e:
            result["error"] = str(e)
            print(f"[MEMORY_TEST] Error during vLLM loading: {e}", file=sys.stderr)
            return result

    except Exception as e:
        result["error"] = str(e)
        print(f"[MEMORY_TEST] Error during setup: {e}", file=sys.stderr)
        return result


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Test vLLM model loading and measure memory"
    )
    parser.add_argument(
        "--model", required=True, help="Path to HuggingFace model directory"
    )
    parser.add_argument(
        "--max-model-len", type=int, required=True, help="Maximum model length"
    )
    parser.add_argument(
        "--quantization",
        type=str,
        default=None,
        help="Quantization format (awq, gptq, or none)",
    )
    parser.add_argument(
        "--gpu-memory-utilization",
        type=float,
        default=0.9,
        help="GPU memory utilization",
    )

    args = parser.parse_args()

    # Validate model path
    if not Path(args.model).exists():
        result = {
            "success": False,
            "ram_mb": None,
            "vram_mb": None,
            "error": f"Model path does not exist: {args.model}",
        }
        print(
            f"[MEMORY_TEST] Model path validation failed: {args.model}", file=sys.stderr
        )
        print(json.dumps(result))
        return 1

    try:
        # Test nvidia-ml-py availability upfront
        import pynvml as nvml

        nvml.nvmlInit()
        nvml.nvmlShutdown()
        print("[MEMORY_TEST] nvidia-ml-py3 is available", file=sys.stderr)
    except ImportError:
        result = {
            "success": False,
            "ram_mb": None,
            "vram_mb": None,
            "error": "nvidia-ml-py3 not available. Install with: pip install nvidia-ml-py3",
        }
        print("[MEMORY_TEST] nvidia-ml-py3 not available", file=sys.stderr)
        print(json.dumps(result))
        return 1
    except Exception as e:
        result = {
            "success": False,
            "ram_mb": None,
            "vram_mb": None,
            "error": f"GPU not available or nvidia-ml-py3 failed: {e}",
        }
        print(f"[MEMORY_TEST] GPU validation failed: {e}", file=sys.stderr)
        print(json.dumps(result))
        return 1

    # Run the test
    print("[MEMORY_TEST] Starting vLLM memory test", file=sys.stderr)
    result = test_vllm_load(
        args.model, args.max_model_len, args.quantization, args.gpu_memory_utilization
    )

    # Output result as JSON on stdout (clean, parseable)
    print("[MEMORY_TEST] Test completed, outputting JSON result", file=sys.stderr)
    print(json.dumps(result))

    return 0 if result["success"] else 1


if __name__ == "__main__":
    sys.exit(main())
