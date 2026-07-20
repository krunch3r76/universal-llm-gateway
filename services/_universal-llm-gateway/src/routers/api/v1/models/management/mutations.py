"""Catalog mutation endpoints for the model management API.

Registers POST/PUT/DELETE handlers on the shared management router for
adding, updating, and deleting catalog entries. Included via app_factory
as management.router.
"""

import asyncio
import json

from fastapi import Depends, HTTPException, Request, Response, status
from fastapi.encoders import jsonable_encoder

try:
    from ......core.catalog_manager import CatalogManager, CatalogValidationError
    from ......schemas.model_management import (
        AddModelRequest,
        ModelManagementResponse,
        UpdateModelRequest,
    )
except ImportError:
    from src.core.catalog_manager import CatalogManager, CatalogValidationError
    from src.schemas.model_management import (
        AddModelRequest,
        ModelManagementResponse,
        UpdateModelRequest,
    )

from .deps import (
    check_auth_token,
    check_management_api_enabled,
    get_catalog_manager_dep,
    logger,
    router,
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
        await asyncio.to_thread(catalog_manager.delete_model, model_key)

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
