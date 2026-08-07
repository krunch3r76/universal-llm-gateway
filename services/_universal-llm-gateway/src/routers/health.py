"""Health check endpoint - /health"""

from deploy_identity.code_version import resolve_code_version
from fastapi import APIRouter
from universal_logging import get_logger

from src import __version__
from src.schemas.responses import HealthResponse
from src.utils.monitoring import system_monitor

router = APIRouter()
logger = get_logger(__name__)


@router.get("/health", response_model=HealthResponse, tags=["Health"])
async def health_check():
    """
    Health check endpoint

    Returns the current health status of the Universal LLM Gateway.

    Response includes service fingerprint for connection validation by Edge Stargates:
    service="universal-llm-gateway", role="gateway".
    """
    status = system_monitor.get_health_status()
    uptime = system_monitor.get_uptime_seconds()

    return HealthResponse(
        status=status,
        service="universal-llm-gateway",
        role="gateway",
        version=__version__,
        uptime_seconds=uptime,
        code_version=resolve_code_version(),
    )
