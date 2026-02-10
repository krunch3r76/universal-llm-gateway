"""Generation parameter profiles configuration endpoints"""

from typing import Any

from fastapi import APIRouter, Depends, Query
from universal_logging import get_logger

from ....profiles import ProfileManager
from ...core.errors import RequestErrorBuilder
from ...dependencies import get_auth_dependency, get_proxy

logger = get_logger(__name__)
router = APIRouter(tags=["profiles"])


def get_profile_manager_from_proxy() -> ProfileManager:
    """Get ProfileManager from proxy (public attribute)."""
    proxy = get_proxy()
    if proxy.profile_manager is None:
        raise RuntimeError("ProfileManager not initialized - startup incomplete")
    return proxy.profile_manager


@router.post("/parameters/profile")
async def set_profile(
    body: dict[str, Any],
    model_id: str | None = Query(
        None, description="Model to apply profile for (optional)"
    ),
    current_user: dict = Depends(get_auth_dependency),
):
    """Set global or model-specific profile (persistent for process lifetime)."""
    profile_name = body.get("profile") or body.get("filter")
    if not profile_name:
        raise RequestErrorBuilder.invalid_request(
            "Missing 'profile' in body", param="profile"
        )

    manager = get_profile_manager_from_proxy()

    # If model-specific, check compatibility using model_info
    compatible = True
    fmt = None
    warnings = []
    parameters = {}

    if model_id:
        # Retrieve model info via proxy
        proxy = get_proxy()
        try:
            # Parse string model_id to ModelId object
            from model_id import ModelId

            parsed_model_id = ModelId.parse(model_id)
            model_config = await proxy.gateway_manager.fetch_model_configuration(
                parsed_model_id
            )
        except Exception as e:
            logger.warning(f"Failed to fetch model information for {model_id}: {e}")
            model_config = None

        compatible, reason = manager.is_model_compatible(model_id, model_config)
        fmt = model_config.format if model_config else None
        try:
            result = manager.set_model_profile(model_id, profile_name)
            parameters = result.get("parameters", {})
        except ValueError as e:
            raise RequestErrorBuilder.invalid_request(str(e), param="profile")
        scope = "model_specific"
    else:
        try:
            result = manager.set_global_profile(profile_name)
            parameters = result.get("parameters", {})
        except ValueError as e:
            raise RequestErrorBuilder.invalid_request(str(e), param="profile")
        scope = "global"

    # Note: compatible will always be True with new multi-engine system
    # Keeping this block for potential future compatibility checks
    if not compatible and model_id:
        msg = (
            f"Profile '{profile_name}' may not be fully compatible "
            f"with model '{model_id}'"
        )
        logger.info(msg)
        warnings.append(msg)

    return {
        "success": True,
        "profile": profile_name,
        "scope": scope,
        "model_id": model_id,
        "compatible": compatible,
        "format": fmt,
        "parameters": parameters,
        "warnings": warnings,
        "active_profiles": manager.get_active_profiles(),
    }


@router.get("/parameters/profile")
async def get_active_profiles(
    current_user: dict = Depends(get_auth_dependency),
):
    """Return current global profile and model-specific overrides."""
    manager = get_profile_manager_from_proxy()
    return manager.get_active_profiles()


@router.delete("/parameters/profile")
async def clear_profile(
    model_id: str | None = Query(
        None, description="Model to clear profile for (optional)"
    ),
    current_user: dict = Depends(get_auth_dependency),
):
    """Clear global or model-specific profile assignment."""
    manager = get_profile_manager_from_proxy()
    if model_id:
        result = manager.clear_model_profile(model_id)
    else:
        result = manager.clear_global_profile()
    result["success"] = True
    result["active_profiles"] = manager.get_active_profiles()
    return result


@router.get("/parameters/profiles")
async def list_available_profiles():
    """List available generation parameter profiles."""
    manager = get_profile_manager_from_proxy()
    return {
        "profiles": manager.get_profile_definitions(),
        "note": (
            "Profiles support both llama-cpp (GGUF) and vLLM (AWQ/GPTQ/HF) "
            "engines with automatic parameter conversion"
        ),
    }


@router.post("/parameters/profiles/reload")
async def reload_profile_configuration(
    current_user: dict = Depends(get_auth_dependency),
):
    """Manually reload profile configuration from profiles.yaml file."""
    try:
        manager = get_profile_manager_from_proxy()
        manager.reload_profiles()
        return {
            "success": True,
            "message": "Profile configuration reloaded successfully",
            "profile_info": manager.get_profile_info(),
        }
    except Exception as e:
        logger.error(f"Failed to reload profiles: {e}")
        raise RequestErrorBuilder.internal_error(
            f"Failed to reload profiles: {str(e)}", operation="reload_profiles"
        )


@router.get("/parameters/profiles/info")
async def get_profile_configuration_info(
    current_user: dict = Depends(get_auth_dependency),
):
    """Get profile configuration info and auto-reload status."""
    manager = get_profile_manager_from_proxy()
    return {"success": True, "profile_info": manager.get_profile_info()}
