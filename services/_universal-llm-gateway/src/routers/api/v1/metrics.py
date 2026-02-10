"""Metrics API endpoints."""

import ipaddress

from fastapi import APIRouter, HTTPException, Request
from universal_logging import get_logger

from ....core.metrics.state_channel_metrics import state_channel_metrics
from ....middleware.auth import WebSocketAuthenticator

logger = get_logger(__name__)
router = APIRouter(prefix="/api/v1/metrics", tags=["metrics"])

authenticator = WebSocketAuthenticator()


def _is_local_subnet(client_ip: str) -> bool:
    """Check if client IP is from a trusted local subnet."""
    try:
        ip = ipaddress.ip_address(client_ip)

        # Allow localhost and local subnets
        local_networks = [
            ipaddress.ip_network("127.0.0.0/8"),  # localhost
            ipaddress.ip_network("10.0.0.0/8"),  # private class A
            ipaddress.ip_network("172.16.0.0/12"),  # private class B
            ipaddress.ip_network("192.168.0.0/16"),  # private class C
            ipaddress.ip_network("::1/128"),  # IPv6 localhost
            ipaddress.ip_network("fc00::/7"),  # IPv6 unique local
        ]

        return any(ip in network for network in local_networks)
    except ValueError:
        # Invalid IP format, deny access
        return False


async def verify_admin_access(request: Request, api_key: str | None = None) -> bool:
    """Verify admin access for metrics endpoints.

    Allows local subnet access without API key authentication.
    External access requires valid API key with metrics.read permission.
    """
    # Get client IP from request
    client_ip = request.client.host

    # Allow local subnet access without API key
    if _is_local_subnet(client_ip):
        logger.debug(f"Allowing local subnet access from {client_ip}")
        return True

    # External access requires API key
    if not api_key:
        raise HTTPException(
            status_code=401, detail="API key required for external access"
        )

    auth_info = await authenticator.authenticate(api_key=api_key)
    if not auth_info or not authenticator.check_permission(auth_info, "metrics.read"):
        raise HTTPException(status_code=403, detail="Insufficient permissions")

    return True


@router.get("/state-channel")
async def get_state_channel_metrics(
    request: Request, api_key: str | None = None
) -> dict:
    """Get detailed metrics for state channel connections.

    Allows local subnet access without authentication.
    External access requires metrics.read permission.

    Returns:
        Comprehensive metrics including connection statistics, message rates, efficiency metrics, active channel details, and bandwidth usage.
    """
    # Perform authorization check
    await verify_admin_access(request, api_key)
    return await state_channel_metrics.get_metrics_summary()


@router.get("/state-channel/cleanup")
async def cleanup_old_metrics(
    request: Request, max_age_hours: int = 1, api_key: str | None = None
) -> dict:
    """Clean up old disconnected channel metrics.

    Allows local subnet access without authentication.
    External access requires metrics.read permission.

    Args:
        max_age_hours: Maximum age in hours for disconnected channels

    Returns:
        Cleanup summary
    """
    # Perform authorization check
    await verify_admin_access(request, api_key)
    result = await state_channel_metrics.cleanup_old_channels(
        max_age_seconds=max_age_hours * 3600
    )
    return {"cleaned_up": result["removed"], "remaining": result["remaining"]}


@router.get("/health")
async def get_health_metrics() -> dict:
    """Get basic health metrics (no authentication required).

    Returns:
        Basic health information
    """
    metrics = await state_channel_metrics.get_metrics_summary()

    return {
        "status": "healthy",
        "state_channels": {
            "active": metrics["connections"]["active"],
            "message_rate": metrics["messages"]["rate_per_second"],
            "error_rate": metrics["messages"]["error_rate_per_second"],
        },
    }
