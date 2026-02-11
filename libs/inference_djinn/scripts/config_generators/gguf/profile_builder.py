"""
Profile Building Module

Constructs SubProfile objects from measurement results.
"""

import sys

from .measurement import (
    find_max_gpu_layers_binary_search,
    test_single_cpu_memory,
    test_single_gpu_layers,
)
from .profiles import SubProfile
from .utils import check_gpu_available, check_gpu_idle, extract_metadata


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
