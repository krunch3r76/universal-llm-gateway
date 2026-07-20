"""
Catalog Module - Model catalog loading, validation, and conversion.

V2 Schema-Per-Engine Architecture:
    - SchemaRegistry: Central registry for engine schemas
    - Schemas handle validation and conversion
    - Device-based configuration (gpu, cpu, hybrid)

Public API:
    - get_catalog_loader(): Get singleton CatalogLoader instance
    - to_model_loaders_format(): Convert to registry format
    - validate_catalog(): Validate catalog entries
    - SchemaRegistry: Access engine schemas

V2 Breaking Changes:
    - No V1 config name constants (GPU_CONFIG_NAMES, DEFAULT_GPU_CONFIG, etc.)
    - ValidationIssue now from schemas module
    - All validation uses schema-driven logic
"""  # noqa: N999

from typing import Any

from .convert import get_all_models_as_loaders_format, to_model_loaders_format
from .loading import CatalogLoader as _CatalogLoaderBase
from .schemas import SchemaRegistry, ValidationIssue
from .validation import (
    get_validation_summary,
    log_validation_report,
    validate_catalog,
    validate_model,
)


class CatalogLoader(_CatalogLoaderBase):
    """
    Unified catalog loader with validation and conversion.

    Extends the base loader with validation and conversion methods.
    """

    def to_model_loaders_format(self, model_id: str) -> dict[str, Any] | None:
        """
        Convert catalog model entry to model_loaders.yaml format.

        Returns:
            Model entry in model_loaders.yaml format, or None if invalid
        """
        return to_model_loaders_format(model_id, self.get_model)

    def get_all_models_as_loaders_format(self) -> dict[str, Any]:
        """
        Convert entire catalog to model_loaders.yaml format.

        Returns:
            Dictionary with 'models' key containing all models in legacy format.
        """
        catalog = self.load()
        return get_all_models_as_loaders_format(catalog, self.get_model)


# ═══════════════════════════════════════════════════════════════════════════════
# SINGLETON INSTANCE
# ═══════════════════════════════════════════════════════════════════════════════

_default_loader: CatalogLoader | None = None


def get_catalog_loader() -> CatalogLoader:
    """
    Get the catalog loader singleton instance.

    Catalog location is determined by workspace root detection.

    Returns:
        CatalogLoader singleton instance
    """
    global _default_loader
    if _default_loader is None:
        _default_loader = CatalogLoader()
    return _default_loader


# Re-export main classes and functions
__all__ = [
    # Loader
    "CatalogLoader",
    "get_catalog_loader",
    # Conversion
    "to_model_loaders_format",
    "get_all_models_as_loaders_format",
    # Validation
    "validate_model",
    "validate_catalog",
    "log_validation_report",
    "get_validation_summary",
    "ValidationIssue",
    # Schemas
    "SchemaRegistry",
]
