"""Resource preflight checks for model loading."""

from typing import TYPE_CHECKING

from universal_logging import get_logger

if TYPE_CHECKING:
    from ...gateway_config import GatewayConfig
    from ..controller import WorkerController


def _get_resource_tracker():
    from src.core.resources import resource_tracker

    return resource_tracker


logger = get_logger(__name__)
structured_logger = get_logger("universal_llm_gateway.preflight")


async def evaluate_resources(
    model_id: str, gateway_config: "GatewayConfig"
) -> tuple[bool, str | None, bool]:
    """
    Evaluate whether resources are sufficient, allowing margin bypass.

    Returns:
        Tuple of (can_load, reason, bypassed_margin)
        - can_load: True when load is allowed
        - reason: Text describing shortfall when applicable
        - bypassed_margin: True when proceeding without configured safety margin
    """
    if not gateway_config.resource_guard.enabled:
        return True, None, False

    resource_tracker = _get_resource_tracker()

    requirements = resource_tracker.get_model_requirements(model_id)
    required_vram = requirements.get("vram_required_mb") or 0
    required_ram = requirements.get("ram_required_mb") or 0

    if not required_vram and not required_ram:
        logger.debug(f"No resource requirements for {model_id}, skipping check")
        return True, None, False

    vram_margin = gateway_config.resource_guard.vram_safety_margin
    ram_margin = gateway_config.resource_guard.ram_safety_margin
    min_vram_mb = gateway_config.resource_guard.min_vram_margin_mb
    min_ram_mb = gateway_config.resource_guard.min_ram_margin_mb

    system = await resource_tracker.get_system_resources()
    available_vram = system.available_vram_mb
    available_ram = system.available_ram_mb

    vram_buffer = max(int(required_vram * vram_margin), min_vram_mb)
    ram_buffer = max(int(required_ram * ram_margin), min_ram_mb)
    total_vram = required_vram + vram_buffer
    total_ram = required_ram + ram_buffer

    margin_ram_ok = available_ram >= total_ram
    margin_vram_ok = required_vram == 0 or available_vram >= total_vram
    if margin_ram_ok and margin_vram_ok:
        return True, None, False

    fits_raw_ram = available_ram >= required_ram
    fits_raw_vram = required_vram == 0 or available_vram >= required_vram

    issues = []
    if not margin_ram_ok:
        issues.append(f"RAM: need {total_ram}MB, have {available_ram}MB")
    if not margin_vram_ok:
        issues.append(f"VRAM: need {total_vram}MB, have {available_vram}MB")
    reason = "; ".join(issues)

    if fits_raw_ram and fits_raw_vram:
        return True, reason, True

    return False, reason, False


async def check_resources_and_block(
    controller: "WorkerController", model_id: str
) -> tuple[bool, dict | None]:
    """
    Check if model fits in available resources and block load if insufficient.
    Stargate orchestrates any required evictions.

    Recommendation #7: Return resource details for observability telemetry.

    This function:
    1. Checks if resource guard is enabled in config
    2. Gets model resource requirements (VRAM/RAM)
    3. Applies safety margins from config
    4. Checks if resources are sufficient
    5. If insufficient, records error and returns details for event emission

    Args:
        controller: WorkerController instance for config and model operations
        model_id: Model identifier to check resources for

    Returns:
        Tuple of (resources_ok: bool, resource_details: dict | None)
        - resources_ok: True if sufficient, False if blocked
        - resource_details: Dict with resource info if blocked, None otherwise
    """
    gateway_config = controller.gateway_config
    ok, reason, bypassed_margin = await evaluate_resources(model_id, gateway_config)

    if ok:
        if bypassed_margin and reason:
            logger.warning(
                f"⚠️ Proceeding without safety margin for {model_id}: {reason}"
            )
        return True, None

    # Gather resource details for observability event
    resource_tracker = _get_resource_tracker()
    requirements = resource_tracker.get_model_requirements(model_id)
    system = await resource_tracker.get_system_resources()

    resource_details = {
        "reason": reason,
        "required_vram_mb": requirements.get("vram_required_mb") or 0,
        "available_vram_mb": system.available_vram_mb,
        "required_ram_mb": requirements.get("ram_required_mb") or 0,
        "available_ram_mb": system.available_ram_mb,
        "bypassed_margin": bypassed_margin,
    }

    logger.warning(f"⚠️ Insufficient resources for {model_id}: {reason}")
    error_reason = f"Insufficient resources: {reason}"
    resource_tracker.set_model_error(model_id, error_reason)

    return False, resource_details
