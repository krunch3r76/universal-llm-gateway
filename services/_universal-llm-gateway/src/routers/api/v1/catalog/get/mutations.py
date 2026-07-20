"""Catalog mutation HTTP routes for reload, profile updates, and context activation.

Handles local catalog writes, registry re-validation, init-cache refresh, and
CATALOG_RELOADED event emission after catalog-changing operations.
"""

from typing import Any

from fastapi import HTTPException, Request

try:
    from ......core.catalog import get_catalog_loader
    from ......core.events import EventBus
except ImportError:
    from src.core.catalog import get_catalog_loader
    from src.core.events import EventBus

from .deps import (
    CATALOG_LOCAL_CONFIG_AVAILABLE,
    CatalogConfigError,
    SchemaVersionError,
    export_model_to_local,
    load_local_catalog,
    logger,
    router,
    save_local_catalog,
)
from .schemas import ActivatedContextsUpdate, ProfileUpdate, UpdateResponse


@router.post("/reload")
async def reload_catalog(request: Request) -> dict[str, Any]:
    """
    Force reload of the catalog from disk.

    Re-validates model file availability to detect added/removed models.

    Returns:
        Status of the reload operation
    """
    try:
        loader = get_catalog_loader()
        loader.reload()
        catalog = loader.load()

        if hasattr(request.app.state, "model_registry"):
            registry = request.app.state.model_registry
            validation_report = registry.validate_model_files(fast_mode=True)

            logger.info(
                f"Re-validated {validation_report.total_models} models: "
                f"{validation_report.valid_models} available, "
                f"{validation_report.total_models - validation_report.valid_models} unavailable"
            )

            if hasattr(request.app.state, "init_cache"):
                init_cache = request.app.state.init_cache
                await init_cache.refresh()
                logger.info("Force-refreshed InitDataCache after validation")

        if hasattr(request.app.state, "event_bus"):
            from src.core.events.types import CatalogReloaded

            event_bus: EventBus = request.app.state.event_bus
            catalog_event = CatalogReloaded(reason="api_reload")
            await event_bus.publish_nowait(catalog_event)
            logger.info("Emitted CATALOG_RELOADED event after API reload")

        return {
            "status": "success",
            "message": "Catalog reloaded",
            "models_count": len(catalog.get("models", {})),
            "schema_version": catalog.get("schema_version", 2),
        }
    except Exception as e:
        logger.error(f"Failed to reload catalog: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to reload catalog: {e}")


@router.patch("/models/{model_id}/profile", response_model=UpdateResponse)
async def update_model_profile(model_id: str, update: ProfileUpdate) -> UpdateResponse:
    """
    Update resource values for a model profile (V2 format).

    Used by measurement tooling to update VRAM/RAM values after actual measurement.
    Exports the model to local catalog if not already there, then updates the profile.

    Automatically updates activated_*_contexts to include the highest context when
    a new profile is added. This ensures newly measured profiles are immediately
    visible in /v1/models without manual activation.

    Args:
        model_id: Model identifier
        update: Profile update with context, device, and resource values

    Returns:
        UpdateResponse with status and updated fields
    """
    if not CATALOG_LOCAL_CONFIG_AVAILABLE:
        raise HTTPException(
            status_code=501,
            detail=(
                "Local catalog management not available "
                "(inference_djinn.catalog not installed)"
            ),
        )

    try:
        loader = get_catalog_loader()
        model = loader.get_model(model_id)

        if not model:
            raise HTTPException(status_code=404, detail=f"Model not found: {model_id}")

        schema_name = model.get("schema")
        if not schema_name:
            raise HTTPException(
                status_code=400,
                detail=f"Model {model_id} missing required 'schema' field (V2 format required)",
            )

        try:
            from ......core.catalog.schemas import SchemaRegistry

            schema = SchemaRegistry.get_by_engine(schema_name)
            if not schema:
                raise HTTPException(
                    status_code=400,
                    detail=f"Unknown schema '{schema_name}' for model {model_id}",
                )

            if update.device not in schema.supported_devices:
                raise HTTPException(
                    status_code=400,
                    detail=(
                        f"Device '{update.device}' not supported by schema '{schema_name}'. "
                        f"Supported devices: {sorted(schema.supported_devices)}"
                    ),
                )
        except ImportError as e:
            logger.warning(
                f"Could not validate device (SchemaRegistry unavailable): {e}"
            )

        try:
            export_model_to_local(model_id, force=False)
            logger.info(f"Exported {model_id} to local catalog for updates")
        except CatalogConfigError as e:
            if "already exists" not in str(e):
                raise

        local = load_local_catalog()
        local_models = local.get("models", {})

        if model_id not in local_models:
            raise HTTPException(
                status_code=500,
                detail=f"Failed to export model {model_id} to local catalog",
            )

        model_entry = local_models[model_id]
        devices = model_entry.setdefault("devices", {})

        device = update.device
        if device not in devices:
            devices[device] = {"profiles": {}}

        profiles = devices[device].setdefault("profiles", {})
        context_key = str(update.context)

        if context_key not in profiles:
            profiles[context_key] = {}
        profile = profiles[context_key]

        updated_fields = []
        if update.vram_mb is not None:
            profile["vram_mb"] = update.vram_mb
            updated_fields.append("vram_mb")
        if update.ram_mb is not None:
            profile["ram_mb"] = update.ram_mb
            updated_fields.append("ram_mb")
        if update.n_gpu_layers is not None:
            profile["n_gpu_layers"] = update.n_gpu_layers
            updated_fields.append("n_gpu_layers")

        metadata = model_entry.setdefault("metadata", {})
        is_cpu_device = device == "cpu"

        if is_cpu_device:
            all_cpu_contexts = set()
            cpu_device = devices.get("cpu", {})
            for ctx in cpu_device.get("profiles", {}).keys():
                all_cpu_contexts.add(int(ctx))
            if all_cpu_contexts:
                highest_cpu = max(all_cpu_contexts)
                current_activated = metadata.get("activated_cpu_contexts") or []
                if not current_activated or highest_cpu > max(current_activated):
                    metadata["activated_cpu_contexts"] = [highest_cpu]
                    updated_fields.append("activated_cpu_contexts")
                    logger.info(
                        f"Auto-activated CPU context {highest_cpu} for {model_id}"
                    )
        else:
            all_gpu_contexts = set()
            for dev_name in ["gpu", "hybrid"]:
                dev_config = devices.get(dev_name, {})
                for ctx in dev_config.get("profiles", {}).keys():
                    all_gpu_contexts.add(int(ctx))
            if all_gpu_contexts:
                highest_gpu = max(all_gpu_contexts)
                current_activated = metadata.get("activated_gpu_contexts") or []
                if not current_activated or highest_gpu > max(current_activated):
                    metadata["activated_gpu_contexts"] = [highest_gpu]
                    updated_fields.append("activated_gpu_contexts")
                    logger.info(
                        f"Auto-activated GPU context {highest_gpu} for {model_id}"
                    )

        save_local_catalog(local)
        loader.reload()

        logger.info(
            f"Updated profile for {model_id} ({device}@{context_key}): {updated_fields}"
        )

        return UpdateResponse(
            status="success",
            message=f"Updated profile {device}@{context_key}",
            model_id=model_id,
            updated_fields=updated_fields,
        )

    except HTTPException:
        raise
    except SchemaVersionError as e:
        raise HTTPException(status_code=400, detail=f"Schema version error: {e}")
    except Exception as e:
        logger.error(f"Failed to update profile for {model_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to update profile: {e}")


@router.patch("/models/{model_id}/activated-contexts", response_model=UpdateResponse)
async def update_activated_contexts(
    model_id: str, update: ActivatedContextsUpdate
) -> UpdateResponse:
    """
    Update activated contexts for a model.

    Controls which context profiles are exposed in /v1/models.
    Exports the model to local catalog if not already there.

    Args:
        model_id: Model identifier
        update: Activated contexts to set

    Returns:
        UpdateResponse with status and updated fields
    """
    if not CATALOG_LOCAL_CONFIG_AVAILABLE:
        raise HTTPException(
            status_code=501,
            detail=(
                "Local catalog management not available "
                "(inference_djinn.catalog not installed)"
            ),
        )

    try:
        loader = get_catalog_loader()
        model = loader.get_model(model_id)

        if not model:
            raise HTTPException(status_code=404, detail=f"Model not found: {model_id}")

        try:
            export_model_to_local(model_id, force=False)
            logger.info(f"Exported {model_id} to local catalog for updates")
        except CatalogConfigError as e:
            if "already exists" not in str(e):
                raise

        local = load_local_catalog()
        local_models = local.get("models", {})

        if model_id not in local_models:
            raise HTTPException(
                status_code=500,
                detail=f"Failed to export model {model_id} to local catalog",
            )

        model_entry = local_models[model_id]
        metadata = model_entry.get("metadata", {})

        updated_fields = []
        if update.activated_gpu_contexts is not None:
            metadata["activated_gpu_contexts"] = update.activated_gpu_contexts
            updated_fields.append("activated_gpu_contexts")
        if update.activated_cpu_contexts is not None:
            metadata["activated_cpu_contexts"] = update.activated_cpu_contexts
            updated_fields.append("activated_cpu_contexts")

        model_entry["metadata"] = metadata
        save_local_catalog(local)
        loader.reload()

        logger.info(f"Updated activated contexts for {model_id}: {updated_fields}")

        return UpdateResponse(
            status="success",
            message="Updated activated contexts",
            model_id=model_id,
            updated_fields=updated_fields,
        )

    except HTTPException:
        raise
    except SchemaVersionError as e:
        raise HTTPException(status_code=400, detail=f"Schema version error: {e}")
    except Exception as e:
        logger.error(f"Failed to update activated contexts for {model_id}: {e}")
        raise HTTPException(
            status_code=500, detail=f"Failed to update activated contexts: {e}"
        )
