"""
Catalog API endpoints.

Provides endpoints for accessing and updating the model catalog, including:
- Full catalog (models only - transformations are Stargate's domain)
- Individual model entries
- Profile updates (VRAM/RAM values from measurement)
- Activated contexts updates

Note: Transformations are NOT part of Gateway's catalog. They belong to Stargate
and are managed separately in stargate/config/model_transformations.yaml.
Gateway is a pure passthrough - no request modification.
"""

from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field
from universal_logging import get_logger

try:
    from ....core.catalog import get_catalog_loader
    from ....core.events import EventBus
except ImportError:
    from src.core.catalog import get_catalog_loader
    from src.core.events import EventBus

try:
    from inference_djinn.catalog.local_config import (
        CatalogConfigError as _CatalogConfigError,
    )
    from inference_djinn.catalog.local_config import (
        SchemaVersionError as _SchemaVersionError,
    )
    from inference_djinn.catalog.local_config import (
        export_model_to_local as _export_model_to_local,
    )
    from inference_djinn.catalog.local_config import (
        load_local_catalog as _load_local_catalog,
    )
    from inference_djinn.catalog.local_config import (
        save_local_catalog as _save_local_catalog,
    )

    CATALOG_LOCAL_CONFIG_AVAILABLE = True
    export_model_to_local = _export_model_to_local
    load_local_catalog = _load_local_catalog
    save_local_catalog = _save_local_catalog
    CatalogConfigError = _CatalogConfigError
    SchemaVersionError = _SchemaVersionError
except ImportError:
    CATALOG_LOCAL_CONFIG_AVAILABLE = False
    export_model_to_local = None  # type: ignore[assignment]
    load_local_catalog = None  # type: ignore[assignment]
    save_local_catalog = None  # type: ignore[assignment]
    CatalogConfigError = Exception  # type: ignore[assignment, misc]
    SchemaVersionError = Exception  # type: ignore[assignment, misc]

logger = get_logger(__name__)

router = APIRouter(prefix="/v1/catalog", tags=["catalog"])


class CatalogResponse(BaseModel):
    """Response model for full catalog endpoint."""

    catalog_version: str
    catalog_type: str
    schema_version: int
    models: dict[str, Any]


class ModelEntryResponse(BaseModel):
    """Response model for individual model entry (V2)."""

    model_config = ConfigDict(populate_by_name=True)

    model_id: str
    schema_name: str = Field(alias="schema")  # V2: required
    metadata: dict[str, Any]
    download: dict[str, Any]
    loader: dict[str, Any] = {}  # V2: replaces base_loader
    devices: dict[str, Any] = {}  # V2: replaces configurations


class ProfileUpdate(BaseModel):
    """Request model for updating a profile's resource values (V2)."""

    context: int = Field(..., description="Context length (e.g., 4096, 8192)")
    device: str = Field(
        "gpu",
        description="Device type: gpu, cpu, or hybrid",
    )
    vram_mb: int | None = Field(None, description="VRAM usage in MB")
    ram_mb: int | None = Field(None, description="RAM usage in MB")
    n_gpu_layers: int | None = Field(None, description="Number of GPU layers")


class ActivatedContextsUpdate(BaseModel):
    """Request model for updating activated contexts."""

    activated_gpu_contexts: list[int] | None = Field(
        None, description="GPU context lengths to expose in /v1/models"
    )
    activated_cpu_contexts: list[int] | None = Field(
        None, description="CPU context lengths to expose in /v1/models"
    )


class UpdateResponse(BaseModel):
    """Response model for update operations."""

    status: str
    message: str
    model_id: str
    updated_fields: list[str] = []


@router.get("", response_model=CatalogResponse)
async def get_catalog(
    include_models: bool = Query(
        True, description="Include models section in response"
    ),
) -> CatalogResponse:
    """
    Get the full merged catalog (static + dynamic).

    Note: Transformations are NOT included - they are Stargate's domain.
    Gateway is a pure passthrough and does not handle request modifications.

    Args:
        include_models: Whether to include the models section (default: True)

    Returns:
        CatalogResponse with catalog data (models only)
    """
    try:
        loader = get_catalog_loader()
        catalog = loader.load()

        return CatalogResponse(
            catalog_version=catalog.get("catalog_version", "1.0"),
            catalog_type=catalog.get("catalog_type", "merged"),
            schema_version=catalog.get("schema_version", 2),
            models=catalog.get("models", {}) if include_models else {},
        )
    except Exception as e:
        logger.error(f"Failed to load catalog: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to load catalog: {e}")


@router.get("/models", response_model=dict[str, Any])
async def get_catalog_models(
    format_filter: str | None = Query(
        None, description="Filter by model format (gguf, awq, hf, gptq)"
    ),
) -> dict[str, Any]:
    """
    Get all models from the catalog.

    Args:
        format_filter: Optional filter by model format

    Returns:
        Dictionary of model entries keyed by model_id
    """
    try:
        loader = get_catalog_loader()

        if format_filter:
            model_ids = loader.list_models_by_format(format_filter)
            models = {mid: loader.get_model(mid) for mid in model_ids}
        else:
            catalog = loader.load()
            models = catalog.get("models", {})

        return {"models": models, "count": len(models)}
    except Exception as e:
        logger.error(f"Failed to load models: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to load catalog: {e}")


class ModelSummary(BaseModel):
    """Simple model summary for listing."""

    model_id: str
    filename: str  # GGUF file or HF directory name
    hf_repo: str | None = None  # HuggingFace repo if available
    format: str
    display_name: str | None = None
    description: str | None = None


class ModelSummaryListResponse(BaseModel):
    """Response model for simple model listing."""

    models: list[ModelSummary]
    count: int


@router.get("/models/list", response_model=ModelSummaryListResponse)
async def list_catalog_models_simple(
    format_filter: str | None = Query(
        None, description="Filter by model format (gguf, awq, hf, gptq)"
    ),
) -> ModelSummaryListResponse:
    """
    Get a simple list of catalog models with ID, filename, and format.

    Useful for finding the correct model_id for measurement jobs.
    Unlike /v1/models which shows synthetic model IDs with context suffixes,
    this returns the base catalog model IDs.

    Args:
        format_filter: Optional filter by model format

    Returns:
        List of ModelSummary with model_id, filename, hf_repo, format
    """
    try:
        loader = get_catalog_loader()

        if format_filter:
            model_ids = loader.list_models_by_format(format_filter)
        else:
            catalog = loader.load()
            model_ids = list(catalog.get("models", {}).keys())

        summaries: list[ModelSummary] = []
        for mid in sorted(model_ids):
            model = loader.get_model(mid)
            if not model:
                continue

            metadata = model.get("metadata", {})
            download = model.get("download", {})
            hf_info = download.get("huggingface", {})

            # Get filename: GGUF uses file, HF/AWQ uses repo name as dir
            hf_file = hf_info.get("file")
            hf_repo = hf_info.get("repo")

            if hf_file:
                filename = hf_file
            elif hf_repo:
                filename = hf_repo.split("/")[-1]
            else:
                filename = mid

            summaries.append(
                ModelSummary(
                    model_id=mid,
                    filename=filename,
                    hf_repo=hf_repo,
                    format=metadata.get("format", "unknown"),
                    display_name=metadata.get("display_name") or metadata.get("name"),
                    description=metadata.get("description"),
                )
            )

        return ModelSummaryListResponse(models=summaries, count=len(summaries))
    except Exception as e:
        logger.error(f"Failed to list models: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to list models: {e}")


@router.get("/models/{model_id}", response_model=ModelEntryResponse)
async def get_catalog_model(model_id: str) -> ModelEntryResponse:
    """
    Get a specific model entry from the catalog.

    Args:
        model_id: Model identifier

    Returns:
        ModelEntryResponse with model data

    Raises:
        HTTPException: 404 if model not found
    """
    try:
        loader = get_catalog_loader()
        model = loader.get_model(model_id)

        if not model:
            raise HTTPException(status_code=404, detail=f"Model not found: {model_id}")

        return ModelEntryResponse(
            model_id=model_id,
            schema_name=model.get("schema", ""),
            metadata=model.get("metadata", {}),
            download=model.get("download", {}),
            loader=model.get("loader", {}),
            devices=model.get("devices", {}),
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to load model {model_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to load catalog: {e}")


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
        loader.reload()  # Clear cache
        catalog = loader.load()  # Load fresh catalog

        # Re-validate model files to detect changes (file additions/removals)
        if hasattr(request.app.state, "model_registry"):
            registry = request.app.state.model_registry

            # Use registry's validate_model_files method (uses internal model_loaders_config with paths)
            validation_report = registry.validate_model_files(fast_mode=True)

            logger.info(
                f"Re-validated {validation_report.total_models} models: "
                f"{validation_report.valid_models} available, "
                f"{validation_report.total_models - validation_report.valid_models} unavailable"
            )

            # Force InitDataCache to refresh NOW (before CATALOG_RELOADED event)
            # This ensures WebSocketEventForwarder gets the updated model list
            if hasattr(request.app.state, "init_cache"):
                init_cache = request.app.state.init_cache
                await init_cache.refresh()
                logger.info("Force-refreshed InitDataCache after validation")

        # Emit CATALOG_RELOADED event for WebSocket forwarder
        if hasattr(request.app.state, "event_bus"):
            from src.core.events.types import CatalogReloaded

            event_bus: EventBus = request.app.state.event_bus
            catalog_event = CatalogReloaded(reason="api_reload")
            await event_bus.publish_async_nowait(catalog_event)
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

        # Validate device is supported by schema
        schema_name = model.get("schema")
        if not schema_name:
            raise HTTPException(
                status_code=400,
                detail=f"Model {model_id} missing required 'schema' field (V2 format required)",
            )

        try:
            from ...core.catalog.schemas import SchemaRegistry

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

        # Ensure model exists in local catalog
        try:
            export_model_to_local(model_id, force=False)
            logger.info(f"Exported {model_id} to local catalog for updates")
        except CatalogConfigError as e:
            if "already exists" not in str(e):
                raise

        # Load local catalog and update profile
        local = load_local_catalog()
        local_models = local.get("models", {})

        if model_id not in local_models:
            raise HTTPException(
                status_code=500,
                detail=f"Failed to export model {model_id} to local catalog",
            )

        model_entry = local_models[model_id]
        devices = model_entry.setdefault("devices", {})

        # Get or create the device
        device = update.device
        if device not in devices:
            devices[device] = {"profiles": {}}

        profiles = devices[device].setdefault("profiles", {})
        context_key = str(update.context)

        # Get or create profile for this context
        if context_key not in profiles:
            profiles[context_key] = {}
        profile = profiles[context_key]

        # Update fields
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

        # Auto-update activated contexts to highest context
        metadata = model_entry.setdefault("metadata", {})
        is_cpu_device = device == "cpu"

        if is_cpu_device:
            # Get all CPU context lengths
            all_cpu_contexts = set()
            cpu_device = devices.get("cpu", {})
            for ctx in cpu_device.get("profiles", {}).keys():
                all_cpu_contexts.add(int(ctx))
            if all_cpu_contexts:
                highest_cpu = max(all_cpu_contexts)
                current_activated = metadata.get("activated_cpu_contexts") or []
                # Update if empty or if we have a higher context
                if not current_activated or highest_cpu > max(current_activated):
                    metadata["activated_cpu_contexts"] = [highest_cpu]
                    updated_fields.append("activated_cpu_contexts")
                    logger.info(
                        f"Auto-activated CPU context {highest_cpu} for {model_id}"
                    )
        else:
            # Get all GPU context lengths (gpu + hybrid)
            all_gpu_contexts = set()
            for dev_name in ["gpu", "hybrid"]:
                dev_config = devices.get(dev_name, {})
                for ctx in dev_config.get("profiles", {}).keys():
                    all_gpu_contexts.add(int(ctx))
            if all_gpu_contexts:
                highest_gpu = max(all_gpu_contexts)
                current_activated = metadata.get("activated_gpu_contexts") or []
                # Update if empty or if we have a higher context
                if not current_activated or highest_gpu > max(current_activated):
                    metadata["activated_gpu_contexts"] = [highest_gpu]
                    updated_fields.append("activated_gpu_contexts")
                    logger.info(
                        f"Auto-activated GPU context {highest_gpu} for {model_id}"
                    )

        # Save local catalog
        save_local_catalog(local)

        # Reload merged catalog
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

        # Ensure model exists in local catalog
        try:
            export_model_to_local(model_id, force=False)
            logger.info(f"Exported {model_id} to local catalog for updates")
        except CatalogConfigError as e:
            if "already exists" not in str(e):
                raise

        # Load local catalog and update metadata
        local = load_local_catalog()
        local_models = local.get("models", {})

        if model_id not in local_models:
            raise HTTPException(
                status_code=500,
                detail=f"Failed to export model {model_id} to local catalog",
            )

        model_entry = local_models[model_id]
        metadata = model_entry.get("metadata", {})

        # Update fields
        updated_fields = []
        if update.activated_gpu_contexts is not None:
            metadata["activated_gpu_contexts"] = update.activated_gpu_contexts
            updated_fields.append("activated_gpu_contexts")
        if update.activated_cpu_contexts is not None:
            metadata["activated_cpu_contexts"] = update.activated_cpu_contexts
            updated_fields.append("activated_cpu_contexts")

        model_entry["metadata"] = metadata

        # Save local catalog
        save_local_catalog(local)

        # Reload merged catalog
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
