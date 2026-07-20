"""Catalog query and reload endpoints for the model management API.

Registers GET config/list and POST reload handlers on the shared management
router. Reload refreshes CatalogManager and ModelRegistry from disk.
"""

import asyncio

from fastapi import Depends, HTTPException, Request, status

try:
    from ......core.catalog_manager import CatalogManager
    from ......schemas.model_management import (
        GetModelConfigResponse,
        ListModelsResponse,
        ModelListItem,
        ReloadConfigRequest,
        ReloadConfigResponse,
    )
except ImportError:
    from src.core.catalog_manager import CatalogManager
    from src.schemas.model_management import (
        GetModelConfigResponse,
        ListModelsResponse,
        ModelListItem,
        ReloadConfigRequest,
        ReloadConfigResponse,
    )

from .deps import (
    check_auth_token,
    check_management_api_enabled,
    get_catalog_manager_dep,
    logger,
    router,
)


@router.get(
    "/v1/models/{model_key}/config",
    response_model=GetModelConfigResponse,
    summary="Get model configuration",
    description="Get the current catalog entry for a specific model",
    dependencies=[Depends(check_management_api_enabled), Depends(check_auth_token)],
)
async def get_model_config(
    model_key: str,
    catalog_manager: CatalogManager = Depends(get_catalog_manager_dep),
):
    """Get catalog entry for a model."""
    try:
        model_config = await asyncio.to_thread(catalog_manager.get_model, model_key)

        if model_config is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={
                    "status": "error",
                    "message": f"Model '{model_key}' not found",
                    "error_type": "not_found",
                },
            )

        version = await asyncio.to_thread(catalog_manager.get_catalog_version)

        return GetModelConfigResponse(
            model_key=model_key, config=model_config, version=version
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting model config: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "status": "error",
                "message": "Internal server error",
                "error_type": "internal_error",
            },
        )


@router.post(
    "/v1/models/reload",
    response_model=ReloadConfigResponse,
    summary="Reload catalog",
    description="Trigger hot-reload of the model catalog and refresh ModelRegistry",
    dependencies=[Depends(check_management_api_enabled), Depends(check_auth_token)],
)
async def reload_config(
    req: Request,
    request: ReloadConfigRequest = None,
    catalog_manager: CatalogManager = Depends(get_catalog_manager_dep),
):
    """Reload catalog from disk and refresh ModelRegistry."""
    try:
        old_models = set(catalog_manager.list_models())

        # Reload catalog
        catalog_manager._loader.reload()

        # Refresh ModelRegistry with new catalog data
        if hasattr(req.app.state, "model_registry"):
            new_config = catalog_manager._loader.get_all_models_as_loaders_format()
            req.app.state.model_registry.model_loaders_config = new_config
            logger.info("ModelRegistry refreshed with updated catalog")

        new_models = set(catalog_manager.list_models())

        added = new_models - old_models
        removed = old_models - new_models
        possibly_modified = old_models & new_models

        version = await asyncio.to_thread(catalog_manager.get_catalog_version)

        return ReloadConfigResponse(
            status="success",
            message=(
                f"Catalog reloaded: {len(added)} added, "
                f"{len(removed)} removed, {len(possibly_modified)} existing"
            ),
            models_added=list(added),
            models_removed=list(removed),
            models_possibly_modified=list(possibly_modified),
            version=version,
        )

    except Exception as e:
        logger.error(f"Error reloading catalog: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "status": "error",
                "message": "Internal server error during reload",
                "error_type": "internal_error",
            },
        )


@router.get(
    "/v1/models/list",
    response_model=ListModelsResponse,
    summary="List all models",
    description="List all models in the catalog with summary information",
    dependencies=[Depends(check_management_api_enabled), Depends(check_auth_token)],
)
async def list_models(
    catalog_manager: CatalogManager = Depends(get_catalog_manager_dep),
):
    """List all models in the catalog."""
    try:
        model_ids = catalog_manager.list_models()
        version = await asyncio.to_thread(catalog_manager.get_catalog_version)

        model_items = []
        for model_id in model_ids:
            model_entry = catalog_manager.get_model(model_id)
            if not model_entry:
                continue

            metadata = model_entry.get("metadata", {})
            model_items.append(
                ModelListItem(
                    model_key=model_id,
                    name=metadata.get("name", model_id),
                    format=metadata.get("format", "unknown"),
                    enabled=True,
                    openai_id=model_id,
                )
            )

        return ListModelsResponse(
            models=model_items, total_count=len(model_items), version=version
        )

    except Exception as e:
        logger.error(f"Error listing models: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "status": "error",
                "message": "Internal server error",
                "error_type": "internal_error",
            },
        )
