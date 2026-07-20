"""Shared router, logging, and optional local-catalog imports for catalog GET API.

Centralizes FastAPI router registration and inference_djinn local catalog bindings
used by catalog query and mutation route modules.
"""

from fastapi import APIRouter
from universal_logging import get_logger

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

__all__ = [
    "CATALOG_LOCAL_CONFIG_AVAILABLE",
    "CatalogConfigError",
    "SchemaVersionError",
    "export_model_to_local",
    "load_local_catalog",
    "logger",
    "router",
    "save_local_catalog",
]
