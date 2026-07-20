"""Queue management API response schemas for Universal LLM Gateway.

Package-shadow of the former monolithic resource_management.py. Re-exports every
Pydantic model, literal alias, and error code so existing import paths remain
unchanged for routers and OpenAPI schema generation.
"""

from .error_codes import ErrorCodes
from .literals import ModelStatus, PriorityLevel
from .load_unload import (
    ModelLoadErrorResponse,
    ModelLoadRequest,
    ModelLoadResponse,
    ModelUnloadRequest,
    ModelUnloadResponse,
)
from .model_status import (
    ModelStatusInfo,
    ModelStatusResponse,
    SingleModelStatusResponse,
)
from .requirements import ModelRequirementsResponse
from .resource_status import (
    ModelDetails,
    ModelResourceUsage,
    ResourceStatusResponse,
)

__all__ = [
    "ErrorCodes",
    "ModelDetails",
    "ModelLoadErrorResponse",
    "ModelLoadRequest",
    "ModelLoadResponse",
    "ModelRequirementsResponse",
    "ModelResourceUsage",
    "ModelStatus",
    "ModelStatusInfo",
    "ModelStatusResponse",
    "ModelUnloadRequest",
    "ModelUnloadResponse",
    "PriorityLevel",
    "ResourceStatusResponse",
    "SingleModelStatusResponse",
]
