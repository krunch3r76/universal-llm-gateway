"""Shared FastAPI dependencies and router for model catalog management API.

Holds the APIRouter instance plus auth/enablement dependencies used by all
management route modules. Imported by mutations and queries which register
handlers on the shared router.
"""

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from universal_logging import get_logger

try:
    from ......core.catalog_manager import CatalogManager, get_catalog_manager
    from ......core.gateway_config import GatewayConfig
except ImportError:
    from src.core.catalog_manager import CatalogManager, get_catalog_manager
    from src.core.gateway_config import GatewayConfig

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
