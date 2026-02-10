"""Context activation filtering logic."""

from __future__ import annotations

from dataclasses import dataclass

from model_id import ModelId
from universal_logging import get_logger

logger = get_logger(__name__)


@dataclass
class ActivationInfo:
    """Activation context information for a model."""

    cpu: list[int] | None = None
    gpu: list[int] | None = None


def filter_by_activation(
    model_ids: set[str],
    activated_contexts: dict[str, ActivationInfo],
    model_profile_resources: dict[str, dict[str, dict[int, dict[str, int]]]],
    gateway_resources: dict[str, dict[str, int]],
) -> set[str]:
    """
    Filter synthetic model IDs based on activated contexts.

    Filtering logic:
    1. If activated contexts explicitly defined: only include those
    2. If no activation but gateway resources known: auto-select highest
    3. Otherwise: include all variants (graceful degradation)

    Args:
        model_ids: Set of synthetic model IDs from gateways
        activated_contexts: Map of base_model -> ActivationInfo
        model_profile_resources: Resource profiles per model/context
        gateway_resources: Available resources per gateway

    Returns:
        Filtered set of model IDs
    """
    auto_selected: dict[str, int | None] = {}
    cache_selected_ctx: dict[str, int] = {}
    cache_selected_model: dict[str, str] = {}
    resource_excluded: set[str] = set()
    filtered = set()

    for model_id in model_ids:
        # Use ModelId for parsing instead of manual string manipulation
        try:
            parsed = ModelId.parse(model_id)
            is_cpu = parsed.is_cpu
            is_hybrid = parsed.is_hybrid
            base_model = parsed.base_id
            context = parsed.context_length
        except ValueError:
            # Not a synthetic ID, include as-is
            filtered.add(model_id)
            continue

        if context is not None:
            activation = activated_contexts.get(base_model)
            if activation:
                activated_list = activation.cpu if is_cpu else activation.gpu
            else:
                activated_list = None

            # Special case: Empty activated_gpu_contexts list means "use defaults"
            # Return highest hybrid + highest full-GPU models
            if activated_list is not None and len(activated_list) == 0:
                # Empty list: select highest hybrid and highest full-GPU
                cache_key_empty = f"{base_model}:empty_list"
                if cache_key_empty not in auto_selected:
                    # Get highest hybrid model (by checking actual -hybrid model IDs)
                    highest_hybrid = _get_highest_hybrid_context(
                        base_model,
                        model_ids,
                        model_profile_resources,
                        gateway_resources,
                    )
                    # Get highest full-GPU model (by checking non-hybrid, non-CPU IDs)
                    highest_gpu = _get_highest_full_gpu_context(
                        base_model,
                        model_ids,
                        model_profile_resources,
                        gateway_resources,
                    )
                    auto_selected[cache_key_empty] = {
                        "hybrid": highest_hybrid,
                        "gpu": highest_gpu,
                    }

                selected = auto_selected.get(cache_key_empty, {})
                hybrid_ctx = selected.get("hybrid")
                gpu_ctx = selected.get("gpu")

                # Add hybrid model if this is it
                if is_hybrid and hybrid_ctx and context == hybrid_ctx:
                    if _context_fits_on_any_gateway(
                        base_model,
                        context,
                        False,
                        model_profile_resources,
                        gateway_resources,
                    ):
                        filtered.add(model_id)
                # Add full-GPU model if this is it
                elif not is_hybrid and not is_cpu and gpu_ctx and context == gpu_ctx:
                    if _context_fits_on_any_gateway(
                        base_model,
                        context,
                        False,
                        model_profile_resources,
                        gateway_resources,
                    ):
                        filtered.add(model_id)
                continue

            if activated_list:
                if context in activated_list:
                    if _context_fits_on_any_gateway(
                        base_model,
                        context,
                        is_cpu,
                        model_profile_resources,
                        gateway_resources,
                    ):
                        filtered.add(model_id)
                    else:
                        resource_excluded.add(model_id)
                continue

            cache_key = f"{base_model}:{'cpu' if is_cpu else 'gpu'}"
            if cache_key not in auto_selected:
                auto_selected[cache_key] = _get_highest_fitting_context(
                    base_model, is_cpu, model_profile_resources, gateway_resources
                )

            selected_ctx = auto_selected.get(cache_key)

            if selected_ctx is None:
                current_ctx = cache_selected_ctx.get(cache_key)
                current_model = cache_selected_model.get(cache_key)

                if current_ctx is None or context > current_ctx:
                    if current_model:
                        filtered.discard(current_model)
                    cache_selected_ctx[cache_key] = context
                    cache_selected_model[cache_key] = model_id
                    filtered.add(model_id)
            elif context == selected_ctx:
                filtered.add(model_id)
        else:
            filtered.add(model_id)

    if auto_selected:
        selections = [f"{k}={v}" for k, v in auto_selected.items() if v is not None]
        if selections:
            logger.debug(f"📊 Auto-selected contexts: {', '.join(selections)}")

    if resource_excluded:
        excluded_list = ", ".join(sorted(resource_excluded)[:5])
        more = (
            f" (+{len(resource_excluded) - 5} more)"
            if len(resource_excluded) > 5
            else ""
        )
        logger.info(
            f"⚠️ Excluded {len(resource_excluded)} activated models "
            f"exceeding gateway capacity: {excluded_list}{more}"
        )

    # After filtering, ensure both variants (hybrid and non-hybrid) are included
    # for each activated context
    final_filtered = set()
    for model_id in filtered:
        final_filtered.add(model_id)
        # If this is a non-hybrid GPU model, also include hybrid variant if it exists
        try:
            parsed = ModelId.parse(model_id)
            if (
                not parsed.is_cpu
                and not parsed.is_hybrid
                and parsed.context_length is not None
            ):
                hybrid_variant = parsed.with_suffix(hybrid=True).synthetic_id
                if hybrid_variant in model_ids:
                    final_filtered.add(hybrid_variant)
        except ValueError:
            # Not a synthetic ID, skip
            pass

    logger.debug(
        f"📋 Filtered {len(model_ids)} → {len(final_filtered)} models by contexts"
    )
    return final_filtered


def _context_fits_on_any_gateway(
    base_model_id: str,
    context: int,
    is_cpu: bool,
    model_profile_resources: dict[str, dict[str, dict[int, dict[str, int]]]],
    gateway_resources: dict[str, dict[str, int]],
) -> bool:
    """Check if context fits on any connected gateway."""
    max_ram, max_vram = _get_max_gateway_capacity(gateway_resources)
    if max_ram == 0 and max_vram == 0:
        return True

    profiles = model_profile_resources.get(base_model_id, {})
    key = "cpu" if is_cpu else "gpu"
    model_profiles = profiles.get(key, {})

    if context not in model_profiles:
        return True

    resources = model_profiles[context]
    vram_needed = resources.get("vram_mb", 0)
    ram_needed = resources.get("ram_mb", 0)

    if is_cpu:
        return ram_needed <= max_ram
    else:
        return vram_needed <= max_vram and ram_needed <= max_ram


def _get_max_gateway_capacity(
    gateway_resources: dict[str, dict[str, int]],
) -> tuple[int, int]:
    """Get maximum RAM and VRAM capacity across all gateways."""
    if not gateway_resources:
        return (0, 0)

    max_ram = max(r["total_ram_mb"] for r in gateway_resources.values())
    max_vram = max(r["total_vram_mb"] for r in gateway_resources.values())
    return (max_ram, max_vram)


def _get_highest_fitting_context(
    base_model_id: str,
    is_cpu: bool,
    model_profile_resources: dict[str, dict[str, dict[int, dict[str, int]]]],
    gateway_resources: dict[str, dict[str, int]],
) -> int | None:
    """Determine highest context that fits on any gateway."""
    max_ram, max_vram = _get_max_gateway_capacity(gateway_resources)
    if max_ram == 0 and max_vram == 0:
        return None

    profiles = model_profile_resources.get(base_model_id, {})
    key = "cpu" if is_cpu else "gpu"
    model_profiles = profiles.get(key, {})

    if not model_profiles:
        return None

    fitting_contexts = []
    for ctx, resources in model_profiles.items():
        vram_needed = resources.get("vram_mb", 0)
        ram_needed = resources.get("ram_mb", 0)

        if is_cpu:
            if ram_needed <= max_ram:
                fitting_contexts.append(ctx)
        else:
            if vram_needed <= max_vram and ram_needed <= max_ram:
                fitting_contexts.append(ctx)

    if fitting_contexts:
        return max(fitting_contexts)
    return None


def _get_highest_hybrid_context(
    base_model_id: str,
    model_ids: set[str],
    model_profile_resources: dict[str, dict[str, dict[int, dict[str, int]]]],
    gateway_resources: dict[str, dict[str, int]],
) -> int | None:
    """
    Get highest context for hybrid models by examining actual model IDs.

    Hybrid models are identified by the -hybrid suffix in their model ID.
    Returns the highest context among hybrid models that fit on available gateways.
    """
    # Find all hybrid model IDs for this base model
    hybrid_contexts = []
    for model_id in model_ids:
        if not model_id.endswith("-hybrid"):
            continue

        # Extract base and context from model_id
        work_id = model_id[:-7]  # Remove -hybrid suffix
        parts = work_id.rsplit("-", 1)
        if len(parts) != 2:
            continue

        extracted_base, context_str = parts
        if extracted_base != base_model_id:
            continue

        try:
            context = int(context_str)
            # Check if this context fits on any gateway
            if _context_fits_on_any_gateway(
                base_model_id,
                context,
                False,
                model_profile_resources,
                gateway_resources,
            ):
                hybrid_contexts.append(context)
        except ValueError:
            continue

    return max(hybrid_contexts) if hybrid_contexts else None


def _get_highest_full_gpu_context(
    base_model_id: str,
    model_ids: set[str],
    model_profile_resources: dict[str, dict[str, dict[int, dict[str, int]]]],
    gateway_resources: dict[str, dict[str, int]],
) -> int | None:
    """
    Get highest context for full-GPU models by examining actual model IDs.

    Full-GPU models are non-hybrid, non-CPU models (no -hybrid or -cpu suffix).
    Returns the highest context among full-GPU models that fit on available gateways.
    """
    # Find all full-GPU model IDs for this base model
    full_gpu_contexts = []
    for model_id in model_ids:
        if model_id.endswith("-hybrid") or model_id.endswith("-cpu"):
            continue

        # Extract base and context from model_id
        parts = model_id.rsplit("-", 1)
        if len(parts) != 2:
            continue

        extracted_base, context_str = parts
        if extracted_base != base_model_id:
            continue

        try:
            context = int(context_str)
            # Check if this context fits on any gateway
            if _context_fits_on_any_gateway(
                base_model_id,
                context,
                False,
                model_profile_resources,
                gateway_resources,
            ):
                full_gpu_contexts.append(context)
        except ValueError:
            continue

    return max(full_gpu_contexts) if full_gpu_contexts else None
