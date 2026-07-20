"""Context list derivation and activation selection for measurement job profiles.

Computes step-down and embedding context sweeps plus post-measurement activated
GPU/CPU context choices from profile result dictionaries.
"""

from typing import Any

from .constants import STANDARD_CONTEXTS


def get_step_down_contexts(training_ctx: int) -> list[int]:
    """
    Get context sizes to try, starting from training_context_length.

    Returns contexts in descending order, starting from the largest
    standard context <= training_ctx.
    """
    contexts = [c for c in STANDARD_CONTEXTS if c <= training_ctx]
    if training_ctx not in contexts and training_ctx > 0:
        contexts.insert(0, training_ctx)
    return contexts


def get_embedding_contexts(training_ctx: int) -> list[int]:
    """
    Get contexts for embedding model measurement.

    Returns training_ctx first plus one standard step-down when available.
    """
    next_below = next((c for c in STANDARD_CONTEXTS if c < training_ctx), None)
    return [training_ctx, next_below] if next_below is not None else [training_ctx]


def determine_activated_contexts(
    results: dict[str, dict[str, Any]], mode: str
) -> tuple[list[int], list[int], str]:
    """
    Determine activated_gpu_contexts and activated_cpu_contexts from results.

    For GPU mode:
      - Activate the largest context that fits entirely on GPU (n_gpu_layers=-1)
      - If none fit entirely, activate the largest successful context
    For CPU mode:
      - Activate the largest successful CPU context

    Returns:
        (activated_gpu_contexts, activated_cpu_contexts, activation_reason)
    """
    gpu_full_offload: list[int] = []
    gpu_partial: list[int] = []
    cpu_contexts: list[int] = []

    for ctx_str, profile in results.items():
        if profile.get("error") or not profile.get("success", True):
            continue
        if profile.get("exceeds_cap"):
            continue

        ctx = int(ctx_str)
        n_layers = profile.get("n_gpu_layers", 0)

        if n_layers == -1:
            gpu_full_offload.append(ctx)
        elif n_layers == 0:
            cpu_contexts.append(ctx)
        else:
            gpu_partial.append(ctx)

    activated_gpu: list[int] = []
    activated_cpu: list[int] = []
    activation_reason = ""

    if mode in ("gpu", "auto"):
        if gpu_full_offload:
            best_ctx = max(gpu_full_offload)
            activated_gpu = [best_ctx]
            activation_reason = f"GPU context {best_ctx} (full offload)"
        elif gpu_partial:
            best_ctx = max(gpu_partial)
            activated_gpu = [best_ctx]
            activation_reason = (
                f"GPU context {best_ctx} (partial offload, no full-GPU fit)"
            )

    if mode in ("cpu", "auto") and cpu_contexts:
        best_ctx = max(cpu_contexts)
        activated_cpu = [best_ctx]
        if activation_reason:
            activation_reason += f", CPU context {best_ctx}"
        else:
            activation_reason = f"CPU context {best_ctx}"

    return activated_gpu, activated_cpu, activation_reason
