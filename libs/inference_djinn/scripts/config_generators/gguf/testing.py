"""
Resource Testing Module

Implements GPU, CPU, and hybrid testing modes for measuring model resource usage.
"""

import json
import os
import signal
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

from .profiles import SubProfile
from .utils import check_gpu_available, check_gpu_idle


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
    Test if a specific n_gpu_layers value works.

    Uses subprocess to safely test layer configuration.

    Args:
        model_path: Path to GGUF model file
        n_layers: Number of GPU layers to test (-1 = all)
        n_ctx: Context length
        n_batch: Batch size
        gpu_index: GPU device index
        timeout_sec: Test timeout in seconds
        mmproj_path: Optional path to mmproj/CLIP file for vision models

    Returns:
        (success, ram_mb, vram_mb) tuple
    """
    # Get path to test script (in libs/inference_djinn/scripts/tests/gguf/)
    test_script = (
        Path(__file__).parent.parent.parent
        / "tests"
        / "gguf"
        / "simple_gpu_layer_test.py"
    )

    if not test_script.exists():
        print(f"Warning: Test script not found: {test_script}", file=sys.stderr)
        return (False, None, None)

    try:
        env = os.environ.copy()
        env["CUDA_VISIBLE_DEVICES"] = str(gpu_index)

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
        ]

        # Add mmproj argument if provided
        if mmproj_path:
            cmd.extend(["--mmproj", mmproj_path])

        result = subprocess.run(
            cmd,
            env=env,
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
    Binary search to find the maximum number of GPU layers that fit in VRAM.

    When n_gpu_layers=-1 (all layers) fails with OOM, this function attempts
    to find the maximum number of layers that can actually fit.

    Args:
        model_path: Path to GGUF model
        n_ctx: Context length
        n_batch: Batch size
        gpu_index: GPU index to use
        max_layers_hint: Upper bound hint from metadata (e.g., block_count)
        mmproj_path: Optional path to mmproj/CLIP file for vision models
        progress_callback: Optional callback(message: str) for progress updates
        min_layers_hint: Minimum layers known to fit (from larger context measurement).
            When stepping down contexts (16384→8192→4096), smaller contexts use less
            KV cache memory, so we know at least this many layers will fit.

    Returns:
        (best_n_layers, ram_mb, vram_mb) tuple
        - best_n_layers: Maximum n_gpu_layers that fit
        - ram_mb, vram_mb: Resource usage (or None if context too large)

    Raises:
        RuntimeError: If context cannot fit even with partial GPU offload
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
            _ram_best, _vram_best = ram, vram
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
    Test CPU-only memory usage.

    Uses subprocess with CUDA_VISIBLE_DEVICES="" to disable GPU.

    Args:
        model_path: Path to GGUF model file
        n_ctx: Context length
        n_batch: Batch size
        mmproj_path: Optional path to mmproj/CLIP file for vision models

    Returns:
        (success, ram_mb, stderr) tuple - stderr contains timing info
    """
    test_script = (
        Path(__file__).parent.parent.parent
        / "tests"
        / "gguf"
        / "simple_cpu_only_memory_test.py"
    )

    if not test_script.exists():
        print(f"Warning: CPU test script not found: {test_script}", file=sys.stderr)
        return (False, None, "")

    try:
        env = os.environ.copy()
        env["CUDA_VISIBLE_DEVICES"] = ""  # Disable all GPUs

        cmd = [
            sys.executable,
            str(test_script),
            "--model",
            model_path,
            "--ctx",
            str(n_ctx),
            "--batch",
            str(n_batch),
        ]

        # Add mmproj argument if provided
        if mmproj_path:
            cmd.extend(["--mmproj", mmproj_path])

        def _create_process_group() -> None:
            """Create isolated process group for clean killpg() support."""
            try:
                os.setpgid(0, 0)
            except OSError:
                pass  # Already group leader or permissions issue

        # Use Popen with process group isolation for clean timeout handling
        proc = subprocess.Popen(
            cmd,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            preexec_fn=_create_process_group,
        )

        # CPU-only tests with use_mmap=False require loading the entire model
        # into RAM at once, which is slower than GPU tests. 300s accommodates
        # larger models (14GB+) on systems with memory pressure.
        cpu_timeout = 150

        try:
            stdout, stderr = proc.communicate(timeout=cpu_timeout)
        except subprocess.TimeoutExpired:
            # Kill the subprocess and its children to free memory
            try:
                # Kill process group (subprocess + any children)
                os.killpg(proc.pid, signal.SIGKILL)
            except (OSError, ProcessLookupError):
                # Fallback to direct kill if process group unavailable
                proc.kill()
            try:
                _ = proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pass  # Process stuck; cleanup will occur on parent exit
            print(
                f"    CPU test timed out after {cpu_timeout}s for context {n_ctx}",
                file=sys.stderr,
            )
            return (False, None, "")

        if proc.returncode != 0:
            if stderr:
                print(
                    f"    CPU test failed for context {n_ctx}: {stderr[:200]}",
                    file=sys.stderr,
                )
            return (False, None, stderr)

        # Subprocess succeeded - parse JSON output
        if not stdout.strip():
            return (False, None, stderr)

        try:
            data = json.loads(stdout.strip())
            return (data.get("success", False), data.get("ram_mb"), stderr)
        except json.JSONDecodeError:
            return (False, None, stderr)

    except Exception as e:
        print(f"    CPU test exception for context {n_ctx}: {e}", file=sys.stderr)
        return (False, None, "")


def build_subprofile_hybrid(
    model_path: str,
    contexts: list[int],
    n_gpu_layers: int,
    safe_margin: int,
    gpu_index: int = 0,
) -> SubProfile:
    """
    Build hybrid (GPU+CPU) SubProfile.

    Tests various n_gpu_layers configurations with safety margins.
    When n_gpu_layers=-1 (all layers) fails with OOM, uses binary search to find
    the maximum number of layers that fit.

    Args:
        model_path: Path to GGUF model
        contexts: List of context lengths (in descending order)
        n_gpu_layers: Target n_gpu_layers (-1 for all, positive for specific count)
        safe_margin: Safety margin to subtract from max layers
        gpu_index: GPU index to use for testing

    Returns:
        SubProfile with measurements
    """
    print("Testing hybrid (GPU+CPU) profile...", file=sys.stderr)

    sub = SubProfile(profile_type="profiles")

    # Check GPU availability
    gpu_available, gpu_info = check_gpu_available()
    if not gpu_available:
        print("Warning: GPU not available, using CPU-only fallback", file=sys.stderr)
        for ctx in contexts:
            sub.add_measurement(ctx, -1, ram_mb=None, vram_mb=None)
        return sub

    # Check GPU is idle
    is_idle, used_mb, total_mb = check_gpu_idle(gpu_index)
    if not is_idle:
        raise RuntimeError(
            f"GPU {gpu_index} not idle ({used_mb}MiB used). "
            f"Free memory or use --gpu-index."
        )

    print(f"GPU {gpu_index} is idle ({used_mb}MiB / {total_mb}MiB)", file=sys.stderr)

    # Extract model block_count (layer hint) from metadata if available
    max_layers_hint = None
    try:
        from .utils import extract_metadata

        meta, _ = extract_metadata(model_path)
        if meta and hasattr(meta, "block_count") and meta.block_count > 0:
            max_layers_hint = meta.block_count
            print(
                f"  Model has {max_layers_hint} blocks (layers) from metadata",
                file=sys.stderr,
            )
    except Exception as e:
        print(
            f"  Warning: Could not extract metadata for layer hint: {e}",
            file=sys.stderr,
        )

    # Test each context in descending order
    for ctx in contexts:
        print(f"  Testing context {ctx}...", file=sys.stderr)

        if n_gpu_layers == -1:
            # Try all layers first
            success, ram, vram = test_single_gpu_layers(
                model_path, -1, ctx, 512, gpu_index
            )
            if success:
                print("    All layers fit on GPU", file=sys.stderr)
                sub.add_measurement(ctx, -1, ram_mb=ram, vram_mb=vram)
            else:
                # All layers don't fit - attempt binary search to find maximum
                print(
                    "    All layers OOM, attempting binary search to find maximum fit...",
                    file=sys.stderr,
                )
                try:
                    max_layers, ram, vram = find_max_gpu_layers_binary_search(
                        model_path, ctx, 512, gpu_index, max_layers_hint=max_layers_hint
                    )

                    # Apply safety margin if requested
                    if safe_margin > 0:
                        reduced_layers = max(0, max_layers - safe_margin)
                        print(
                            f"    Applying safety margin: {max_layers} - {safe_margin} = {reduced_layers} layers",
                            file=sys.stderr,
                        )
                        max_layers = reduced_layers
                        if max_layers > 0:
                            # Verify the reduced layer count works
                            success, ram, vram = test_single_gpu_layers(
                                model_path, max_layers, ctx, 512, gpu_index
                            )

                    sub.add_measurement(ctx, max_layers, ram_mb=ram, vram_mb=vram)
                except RuntimeError as e:
                    # Context cannot fit even with partial GPU offload
                    print(f"    ❌ {e}", file=sys.stderr)
                    # Don't record this configuration - let it be marked as failed/untestable
                    sub.add_measurement(ctx, -1, ram_mb=None, vram_mb=None)
                    # Re-raise to signal that caching should not happen
                    raise
        else:
            # Test specific layer count
            if len(contexts) > 1:
                raise ValueError(
                    "GPU layer testing with specific n_gpu_layers only supports single context"
                )

            success, ram, vram = test_single_gpu_layers(
                model_path, n_gpu_layers, ctx, 512, gpu_index
            )
            if success:
                print(
                    f"    Tested {n_gpu_layers} GPU layers: RAM={ram}MB, VRAM={vram}MB",
                    file=sys.stderr,
                )
                sub.add_measurement(ctx, n_gpu_layers, ram_mb=ram, vram_mb=vram)
            else:
                print(
                    f"    Testing failed for {n_gpu_layers} GPU layers", file=sys.stderr
                )
                # Don't add a measurement with None values - this will be caught by validation
                # Adding None measurement creates invalid configs that should not be output
                raise RuntimeError(
                    f"GPU layer testing failed: {n_gpu_layers} layers cannot fit in VRAM for context {ctx}. "
                    f"Try a lower --n_gpu_layers value or use -1 for automatic layer detection."
                )

    return sub


def build_subprofile_gpu_only(
    model_path: str, contexts: list[int], gpu_index: int = 0
) -> SubProfile:
    """
    Build GPU-only SubProfile.

    Tests with all layers on GPU, logs errors if OOM but continues.

    Args:
        model_path: Path to GGUF model
        contexts: List of context lengths (in descending order)
        gpu_index: GPU index to use for testing

    Returns:
        SubProfile with measurements
    """
    print("Testing GPU-only profile...", file=sys.stderr)

    sub = SubProfile(profile_type="profiles")

    gpu_available, gpu_info = check_gpu_available()
    if not gpu_available:
        raise RuntimeError("GPU not available for GPU-only testing")

    is_idle, used_mb, total_mb = check_gpu_idle(gpu_index)
    if not is_idle:
        raise RuntimeError(
            f"GPU {gpu_index} not idle ({used_mb}MiB used). "
            f"Free memory or use --gpu-index."
        )

    print(f"GPU {gpu_index} is idle ({used_mb}MiB / {total_mb}MiB)", file=sys.stderr)

    # Test each context in descending order with all layers on GPU
    for ctx in contexts:
        print(f"  Testing context {ctx}...", file=sys.stderr)

        success, ram, vram = test_single_gpu_layers(model_path, -1, ctx, 512, gpu_index)
        if success:
            print(f"    Success: RAM={ram}MB, VRAM={vram}MB", file=sys.stderr)
            sub.add_measurement(ctx, -1, ram_mb=ram, vram_mb=vram)
        else:
            print(
                f"    Error: OOM for context {ctx} - continuing with other contexts",
                file=sys.stderr,
            )
            sub.add_measurement(ctx, -1, ram_mb=None, vram_mb=None)

    return sub


def build_subprofile_cpu_only(model_path: str, contexts: list[int]) -> SubProfile:
    """
    Build CPU-only SubProfile.

    Tests with CUDA_VISIBLE_DEVICES="" and n_gpu_layers=0.
    Logs errors if OOM but continues.

    Args:
        model_path: Path to GGUF model
        contexts: List of context lengths (in descending order)

    Returns:
        SubProfile with measurements
    """
    print("Testing CPU-only profile...", file=sys.stderr)

    sub = SubProfile(profile_type="cpu_profiles")

    # Test each context in descending order
    for ctx in contexts:
        print(f"  Testing context {ctx}...", file=sys.stderr)

        success, ram, _stderr = test_single_cpu_memory(model_path, ctx, 512)
        if success:
            print(f"    Success: RAM={ram}MB", file=sys.stderr)
            sub.add_measurement(ctx, 0, ram_mb=ram, vram_mb=0)
        else:
            print(
                f"    Error: OOM for context {ctx} - continuing with other contexts",
                file=sys.stderr,
            )
            sub.add_measurement(ctx, 0, ram_mb=None, vram_mb=0)

    return sub
