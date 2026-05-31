"""
Model Configuration Management API Router

Provides HTTP endpoints for programmatic model catalog management.
Secured by gateway config and optional token authentication.

Uses catalog format (metadata, download, configurations) for all operations.
Writes to dynamic catalog by default, static with static=true flag.
"""

import asyncio
import json

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response, status
from fastapi.encoders import jsonable_encoder
from universal_logging import get_logger

try:
    from .....core.catalog_manager import (
        CatalogManager,
        CatalogValidationError,
        get_catalog_manager,
    )
    from .....core.gateway_config import GatewayConfig
    from .....schemas.model_management import (
        AddModelRequest,
        GetModelConfigResponse,
        ListModelsResponse,
        ModelListItem,
        ModelManagementResponse,
        ReloadConfigRequest,
        ReloadConfigResponse,
        UpdateModelRequest,
    )
except ImportError:
    from src.core.catalog_manager import (
        CatalogManager,
        CatalogValidationError,
        get_catalog_manager,
    )
    from src.core.gateway_config import GatewayConfig
    from src.schemas.model_management import (
        AddModelRequest,
        GetModelConfigResponse,
        ListModelsResponse,
        ModelListItem,
        ModelManagementResponse,
        ReloadConfigRequest,
        ReloadConfigResponse,
        UpdateModelRequest,
    )

logger = get_logger(__name__)

router = APIRouter()


def get_catalog_manager_dep() -> CatalogManager:
    """Dependency to get CatalogManager instance."""
    return get_catalog_manager()


def get_gateway_config(request: Request) -> GatewayConfig:
    """Dependency to get gateway config from app state."""
    return request.app.state.gateway_config


def check_management_api_enabled(
    gateway_config: GatewayConfig = Depends(get_gateway_config),
):
    """Check if management API is enabled."""
    if not gateway_config.management_api.enabled:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "status": "error",
                "message": "Management API is disabled",
                "error_type": "permission_denied",
                "hint": "Set management_api.enabled: true in gateway_config.yaml",
            },
        )


def check_auth_token(
    gateway_config: GatewayConfig = Depends(get_gateway_config),
    x_management_token: str = Header(None),
):
    """Check authentication token if configured."""
    if not gateway_config.management_api.require_token:
        return

    if not gateway_config.management_api.token:
        logger.error("management_api.require_token is true but no token is configured")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "status": "error",
                "message": "Management API authentication is misconfigured",
                "error_type": "configuration_error",
            },
        )

    if not x_management_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={
                "status": "error",
                "message": "Authentication required",
                "error_type": "authentication_required",
            },
        )

    if x_management_token != gateway_config.management_api.token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={
                "status": "error",
                "message": "Invalid authentication token",
                "error_type": "invalid_token",
            },
        )


@router.post(
    "/v1/models",
    response_model=ModelManagementResponse,
    summary="Add or update model",
    description=(
        "Add a new model to the catalog or update existing model. "
        "Accepts catalog format (metadata, download, configurations). "
        "Use static=true to write to static catalog (maintainer mode). "
        "Returns 201 for new models, 200 for updates."
    ),
    dependencies=[Depends(check_management_api_enabled), Depends(check_auth_token)],
)
async def add_model(
    request: AddModelRequest,
    req: Request,
    catalog_manager: CatalogManager = Depends(get_catalog_manager_dep),
):
    """Add a new model or update existing model in the catalog."""
    try:
        result = await asyncio.to_thread(
            catalog_manager.upsert_model,
            model_id=request.model_key,
            entry=request.config,
            allow_overwrite=request.allow_overwrite,
            static=request.static,
        )

        # Auto-refresh ModelRegistry so /v1/models reflects the change immediately
        if hasattr(req.app.state, "model_registry"):
            new_config = catalog_manager._loader.get_all_models_as_loaders_format()
            req.app.state.model_registry.model_loaders_config = new_config

        version = await asyncio.to_thread(
            catalog_manager.get_catalog_version, request.static
        )

        result_dict = result.to_dict()

        response_status = (
            status.HTTP_201_CREATED
            if result.operation == "created"
            else status.HTTP_200_OK
        )

        response_data = ModelManagementResponse(
            status=result_dict["status"],
            message=result_dict["message"],
            model_key=result_dict["model_id"],
            version=version,
        )

        return Response(
            content=json.dumps(jsonable_encoder(response_data)),
            status_code=response_status,
            media_type="application/json",
        )

    except CatalogValidationError as e:
        error_msg = str(e)

        if "already exists" in error_msg:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "status": "error",
                    "message": error_msg,
                    "error_type": "conflict",
                },
            )

        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "status": "error",
                "message": "Catalog validation failed",
                "details": [{"message": error_msg}],
                "error_type": "validation_error",
            },
        )

    except Exception as e:
        logger.error(f"Error adding/updating model: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "status": "error",
                "message": "Internal server error",
                "error_type": "internal_error",
            },
        )


@router.put(
    "/v1/models/{model_key}",
    response_model=ModelManagementResponse,
    summary="Update model",
    description="Update an existing model in the dynamic catalog",
    dependencies=[Depends(check_management_api_enabled), Depends(check_auth_token)],
)
async def update_model(
    model_key: str,
    request: UpdateModelRequest,
    req: Request,
    catalog_manager: CatalogManager = Depends(get_catalog_manager_dep),
):
    """Update an existing model in the dynamic catalog."""
    try:
        result = await asyncio.to_thread(
            catalog_manager.upsert_model,
            model_id=model_key,
            entry=request.config,
            allow_overwrite=True,
        )

        version = await asyncio.to_thread(catalog_manager.get_catalog_version)
        result_dict = result.to_dict()

        return ModelManagementResponse(
            status=result_dict["status"],
            message=result_dict["message"],
            model_key=result_dict["model_id"],
            version=version,
        )

    except CatalogValidationError as e:
        error_msg = str(e)

        if "not found" in error_msg:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={
                    "status": "error",
                    "message": error_msg,
                    "error_type": "not_found",
                },
            )

        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "status": "error",
                "message": "Catalog validation failed",
                "details": [{"message": error_msg}],
                "error_type": "validation_error",
            },
        )

    except Exception as e:
        logger.error(f"Error updating model: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "status": "error",
                "message": "Internal server error",
                "error_type": "internal_error",
            },
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


@router.delete(
    "/v1/models/{model_key}",
    response_model=ModelManagementResponse,
    summary="Delete model",
    description="Delete a model from the dynamic catalog",
    dependencies=[Depends(check_management_api_enabled), Depends(check_auth_token)],
)
async def delete_model(
    model_key: str,
    req: Request,
    catalog_manager: CatalogManager = Depends(get_catalog_manager_dep),
):
    """Delete a model from the dynamic catalog."""
    try:
        result = await asyncio.to_thread(catalog_manager.delete_model, model_key)

        version = await asyncio.to_thread(catalog_manager.get_catalog_version)

        return ModelManagementResponse(
            status="success",
            message=f"Model '{model_key}' deleted successfully",
            model_key=model_key,
            version=version,
        )

    except CatalogValidationError as e:
        error_msg = str(e)

        if "not found" in error_msg:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={
                    "status": "error",
                    "message": error_msg,
                    "error_type": "not_found",
                },
            )

        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "status": "error",
                "message": error_msg,
                "error_type": "validation_error",
            },
        )

    except Exception as e:
        logger.error(f"Error deleting model: {e}", exc_info=True)
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
