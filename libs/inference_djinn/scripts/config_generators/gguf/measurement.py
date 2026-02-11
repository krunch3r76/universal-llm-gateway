"""
Subprocess Measurement Module

GPU, CPU, and binary-search layer tests via subprocess spawning.
"""

import json
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path


def test_single_gpu_layers(
    model_path: str,
    n_layers: int,
    n_ctx: int,
    n_batch: int,
    gpu_index: int,
    timeout_sec: int = 120,
    mmproj_path: str | None = None,
) -> tuple[bool, int | None, int | None]:
    """
    Test if n_gpu_layers value works via subprocess.

    Returns (success, ram_mb, vram_mb).
    """
    test_script = (
        Path(__file__).parent.parent.parent
        / "tests"
        / "gguf"
        / "llama_server_measurement.py"
    )

    if not test_script.exists():
        print(f"Warning: Test script not found: {test_script}", file=sys.stderr)
        return (False, None, None)

    try:
        cmd = [
            sys.executable,
            str(test_script),
            "--model",
            model_path,
            "--layers",
            str(n_layers),
            "--ctx",
            str(n_ctx),
            "--batch",
            str(n_batch),
            "--gpu-index",
            str(gpu_index),
            "--mode",
            "gpu",
        ]

        if mmproj_path:
            cmd.extend(["--mmproj", mmproj_path])

        result = subprocess.run(
            cmd,
            capture_output=True,
            timeout=timeout_sec,
            text=True,
        )

        if result.returncode != 0:
            print(
                f"    Test subprocess failed (exit {result.returncode})",
                file=sys.stderr,
            )
            if result.stderr:
                # Print full stderr to diagnose false negatives
                print(f"    stderr: {result.stderr}", file=sys.stderr)
            return (False, None, None)

        # Subprocess succeeded - parse JSON output
        if not result.stdout.strip():
            print("    Test produced no output", file=sys.stderr)
            return (False, None, None)

        try:
            data = json.loads(result.stdout.strip())
            success = data.get("success", False)

            # If test failed and error message provided, log it
            if not success and data.get("error"):
                print(f"    Test error: {data['error']}", file=sys.stderr)

            return (
                success,
                data.get("ram_mb"),
                data.get("vram_mb"),
            )
        except json.JSONDecodeError:
            print("    Test produced invalid JSON output", file=sys.stderr)
            return (False, None, None)

    except subprocess.TimeoutExpired:
        print(
            f"    Test timed out after {timeout_sec}s (likely OOM or very slow)",
            file=sys.stderr,
        )
        return (False, None, None)
    except Exception as e:
        print(f"    Test failed: {e}", file=sys.stderr)
        return (False, None, None)


def find_max_gpu_layers_binary_search(
    model_path: str,
    n_ctx: int,
    n_batch: int,
    gpu_index: int,
    max_layers_hint: int | None = None,
    mmproj_path: str | None = None,
    progress_callback: Callable[[str], None] | None = None,
    min_layers_hint: int | None = None,
) -> tuple[int, int | None, int | None]:
    """
    Binary search for max GPU layers that fit in VRAM.

    When n_gpu_layers=-1 fails with OOM, finds the maximum layers that fit.
    min_layers_hint: known fit from larger context (smaller ctx uses less KV cache).

    Returns:
        (best_n_layers, ram_mb, vram_mb). Raises RuntimeError if context too large.
    """

    def _log(msg: str) -> None:
        """Log to callback if available, otherwise stderr."""
        if progress_callback:
            progress_callback(msg)
        else:
            print(msg, file=sys.stderr)

    _log(f"  Binary search for maximum GPU layers (context {n_ctx})...")

    if not max_layers_hint:
        raise RuntimeError("Cannot determine model layer count - metadata missing")

    # Validate and clamp min_layers_hint if provided
    if min_layers_hint and min_layers_hint > 0:
        if min_layers_hint > max_layers_hint:
            _log(
                f"    WARNING: min_layers_hint ({min_layers_hint}) exceeds max_layers_hint ({max_layers_hint}), clamping to max"
            )
            min_layers_hint = max_layers_hint

        # Short-circuit if min equals max (we already know the answer)
        if min_layers_hint == max_layers_hint:
            _log(f"    Min/max both {max_layers_hint} layers, testing directly...")
            success, ram, vram = test_single_gpu_layers(
                model_path,
                max_layers_hint,
                n_ctx,
                n_batch,
                gpu_index,
                mmproj_path=mmproj_path,
            )
            if success:
                _log(f"    ✅ {max_layers_hint} layers fit")
                return (max_layers_hint, ram, vram)
            else:
                raise RuntimeError(
                    f"Context {n_ctx} too large: even with max_layers_hint={max_layers_hint} (from previous context), the model fails to load. This indicates metadata may be incorrect or GPU memory fragmentation."
                )

    # Determine lower bound for binary search
    # If we have a hint from a larger context, use it (smaller contexts use less KV cache)
    # Otherwise start with minimal 4 layers
    if min_layers_hint and min_layers_hint > 0:
        minimal_layers = min_layers_hint
        _log(
            f"    Starting from {minimal_layers} layers (known to fit from larger context)"
        )
        # Skip verification test - we know this works from previous measurement
        low = minimal_layers
        ram, vram = None, None
    else:
        minimal_layers = 4
        ram, vram = None, None

        _log(f"    Testing minimal {minimal_layers} layers...")
        success, ram, vram = test_single_gpu_layers(
            model_path,
            minimal_layers,
            n_ctx,
            n_batch,
            gpu_index,
            mmproj_path=mmproj_path,
        )

        if not success:
            # Even minimal layers failed - context is truly too large
            raise RuntimeError(
                f"Context length {n_ctx} too large: even {minimal_layers} GPU layers "
                f"cannot load. Retry with a smaller context using --contexts argument."
            )

        _log(f"    Minimal {minimal_layers} layers successful, starting binary search")
        low = minimal_layers

    # Establish upper bound - try a reasonable starting point
    if max_layers_hint:
        # If we have a hint (e.g., from model params), try it first
        start_high = max_layers_hint
    else:
        # Otherwise use a conservative estimate (e.g., 80 layers for typical 70B model)
        start_high = 80

    # Binary search bounds
    high = None

    # First, find an upper bound that fails
    test_layers = start_high
    while test_layers > low:
        success, ram, vram = test_single_gpu_layers(
            model_path, test_layers, n_ctx, n_batch, gpu_index, mmproj_path=mmproj_path
        )
        if success:
            # Still fits, try more
            low = test_layers
            test_layers *= 2
        else:
            # Doesn't fit, found upper bound
            high = test_layers
            break

    if high is None:
        # All layers fit (all the way up to test_layers)
        _log(f"    All {low} layers fit on GPU (can load more)")
        # Get final measurements for the best configuration
        success, ram, vram = test_single_gpu_layers(
            model_path, low, n_ctx, n_batch, gpu_index, mmproj_path=mmproj_path
        )
        return (low, ram, vram)

    # Binary search between low and high
    _log(f"    Binary search in range [{low}, {high}]...")
    while low + 1 < high:
        mid = (low + high) // 2
        _log(f"      Testing {mid} layers...")
        success, ram, vram = test_single_gpu_layers(
            model_path, mid, n_ctx, n_batch, gpu_index, mmproj_path=mmproj_path
        )
        if success:
            low = mid
            _log(f"      ✓ {mid} layers fit (new lower bound)")
        else:
            high = mid
            _log(f"      ✗ {mid} layers OOM (new upper bound)")

    _log(f"    Found maximum: {low} layers fit on GPU")
    # Get final measurements for the best configuration
    success, ram, vram = test_single_gpu_layers(
        model_path, low, n_ctx, n_batch, gpu_index, mmproj_path=mmproj_path
    )

    return (low, ram, vram)


def test_single_cpu_memory(
    model_path: str,
    n_ctx: int,
    n_batch: int,
    mmproj_path: str | None = None,
) -> tuple[bool, int | None, str]:
    """
    Test CPU-only memory via subprocess.

    Returns (success, ram_mb, stderr).
    """
    test_script = (
        Path(__file__).parent.parent.parent
        / "tests"
        / "gguf"
        / "llama_server_measurement.py"
    )

    if not test_script.exists():
        print(f"Warning: CPU test script not found: {test_script}", file=sys.stderr)
        return (False, None, "")

    cpu_timeout = 150
    try:
        cmd = [
            sys.executable,
            str(test_script),
            "--model",
            model_path,
            "--ctx",
            str(n_ctx),
            "--batch",
            str(n_batch),
            "--mode",
            "cpu",
        ]

        if mmproj_path:
            cmd.extend(["--mmproj", mmproj_path])

        result = subprocess.run(
            cmd,
            capture_output=True,
            timeout=cpu_timeout,
            text=True,
        )

        stderr = result.stderr or ""

        if result.returncode != 0:
            if stderr:
                print(
                    f"    CPU test failed for context {n_ctx}: {stderr[:200]}",
                    file=sys.stderr,
                )
            return (False, None, stderr)

        if not result.stdout.strip():
            return (False, None, stderr)

        try:
            data = json.loads(result.stdout.strip())
            return (data.get("success", False), data.get("ram_mb"), stderr)
        except json.JSONDecodeError:
            return (False, None, stderr)

    except subprocess.TimeoutExpired:
        print(
            f"    CPU test timed out after {cpu_timeout}s for context {n_ctx}",
            file=sys.stderr,
        )
        return (False, None, "")
    except Exception as e:
        print(f"    CPU test exception for context {n_ctx}: {e}", file=sys.stderr)
        return (False, None, "")
