"""Worker dependency validation before model load operations proceed."""

from typing import TYPE_CHECKING

from universal_logging import get_logger

from .constants import get_resource_tracker

if TYPE_CHECKING:
    from ...controller import WorkerController

logger = get_logger(__name__)


async def validate_dependencies(controller: "WorkerController", model_id: str) -> bool:
    """Validate worker dependencies."""
    from ...utils import get_python_executable, validate_worker_dependencies

    is_valid, missing = validate_worker_dependencies(
        get_python_executable(controller.gateway_config)
    )
    if not is_valid:
        error_msg = f"Missing: {', '.join(missing)}"
        logger.error(f"❌ {error_msg}")
        get_resource_tracker().set_model_error(model_id, error_msg)
    return is_valid
