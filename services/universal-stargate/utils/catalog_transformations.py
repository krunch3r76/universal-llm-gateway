"""
Catalog-Aware Transformation Lookup

This module provides transformation lookup that uses the model catalog
as the primary source, with fallback to the legacy model_transformations.yaml.

Architecture:
    1. Check catalog for model's metadata.transformation field
    2. Look up transformation definition in catalog.transformations section
    3. Fall back to legacy model_transformations.yaml if not in catalog

This enables gradual migration from the legacy transformation system
to the catalog-based approach.
"""

from pathlib import Path
from typing import Any

import yaml
from universal_logging import get_logger

from .model_basename import get_model_transformation as legacy_get_transformation

logger = get_logger(__name__)


class CatalogTransformationProvider:
    """
    Provides transformations from model catalog with legacy fallback.

    Priority:
        1. Catalog metadata.transformation reference
        2. Catalog transformations section
        3. Legacy model_transformations.yaml (via UnifiedModelMatcher)

    Caching:
        - Transformations are cached after first load (they are static)
        - Model-to-transformation mappings are cached per model_id
        - Cache is invalidated only on explicit reload() or set_gateway_catalog()
    """

    def __init__(self, catalog_path: str | Path | None = None):
        """
        Initialize catalog transformation provider.

        Args:
            catalog_path: Path to model_catalog.yaml. If None, attempts to load
                         from Gateway via API or uses default path.
        """
        self.catalog_path = Path(catalog_path) if catalog_path else None
        self._catalog: dict[str, Any] | None = None
        self._gateway_catalog: dict[str, Any] | None = None
        # Cache for model -> transformation lookups (immutable once loaded)
        self._transformation_cache: dict[str, dict[str, Any] | None] = {}

    @property
    def catalog(self) -> dict[str, Any]:
        """Load catalog lazily."""
        if self._catalog is None:
            self._catalog = self._load_catalog()
        return self._catalog

    def _load_catalog(self) -> dict[str, Any]:
        """Load catalog from file or gateway."""
        # Try loading from gateway cache first
        if self._gateway_catalog:
            return self._gateway_catalog

        # Try loading from file
        if self.catalog_path and self.catalog_path.exists():
            try:
                with open(self.catalog_path) as f:
                    catalog = yaml.safe_load(f) or {}
                logger.info(f"Loaded catalog from {self.catalog_path}")
                return catalog
            except Exception as e:
                logger.warning(f"Failed to load catalog from file: {e}")

        # Return empty catalog - will use legacy fallback
        return {"transformations": {}, "models": {}}

    def set_gateway_catalog(self, catalog: dict[str, Any]) -> None:
        """
        Set catalog data received from Gateway.

        This allows Stargate to receive catalog data from Gateway's metadata
        endpoint without direct file access.

        Args:
            catalog: Catalog dictionary from Gateway
        """
        self._gateway_catalog = catalog
        self._catalog = None  # Force reload
        self._transformation_cache.clear()  # Clear model->transformation cache

    def get_transformation_for_model(
        self, model_id: str, model_metadata: dict[str, Any] | None = None
    ) -> dict[str, Any] | None:
        """
        Get transformation configuration for a model.

        Priority:
            1. Model metadata.transformation from catalog/gateway metadata
            2. Catalog transformations lookup
            3. Legacy model_transformations.yaml fallback

        Results are cached per model_id for performance.

        Args:
            model_id: Model identifier
            model_metadata: Optional model metadata from Gateway. If provided,
                           uses metadata.transformation directly.

        Returns:
            Transformation configuration dictionary, or None if no transformation
                needed.
        """
        # Check cache first (unless model_metadata override provided)
        if model_id in self._transformation_cache and not model_metadata:
            return self._transformation_cache[model_id]

        result: dict[str, Any] | None = None

        # Priority 1: Check model_metadata.transformation from Gateway
        if model_metadata:
            transform_name = model_metadata.get("transformation")
            if transform_name:
                transform = self._get_transformation_by_name(transform_name)
                if transform:
                    logger.debug(
                        f"Using transformation '{transform_name}' from metadata "
                        f"for {model_id}"
                    )
                    result = transform

        # Priority 2: Check catalog for model entry
        if result is None:
            catalog_model = self.catalog.get("models", {}).get(model_id)
            if catalog_model:
                transform_name = catalog_model.get("metadata", {}).get("transformation")
                if transform_name:
                    transform = self._get_transformation_by_name(transform_name)
                    if transform:
                        logger.debug(
                            f"Using transformation '{transform_name}' from catalog "
                            f"for {model_id}"
                        )
                        result = transform

        # Priority 3: Legacy fallback
        if result is None:
            legacy = legacy_get_transformation(model_id)
            if legacy:
                logger.debug(f"Using legacy transformation for {model_id}")
                result = legacy

        if result is None:
            logger.debug(f"No transformation found for {model_id}")

        # Cache result (even None to avoid repeated lookups)
        self._transformation_cache[model_id] = result
        return result

    def _get_transformation_by_name(self, name: str) -> dict[str, Any] | None:
        """
        Look up transformation by name in catalog.

        Args:
            name: Transformation name

        Returns:
            Transformation configuration, or None if not found
        """
        transformations = self.catalog.get("transformations", {})
        return transformations.get(name)

    def get_transformation_settings(
        self, model_id: str, model_metadata: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """
        Get transformation settings for a model.

        This is a convenience method that returns the settings dict directly,
        with sensible defaults if no transformation is configured.

        Args:
            model_id: Model identifier
            model_metadata: Optional model metadata from Gateway

        Returns:
            Settings dictionary for transformation
        """
        transform = self.get_transformation_for_model(model_id, model_metadata)
        if transform:
            return transform.get("settings", {})
        return {}

    def list_transformations(self) -> list[str]:
        """List all available transformation names."""
        return list(self.catalog.get("transformations", {}).keys())

    def reload(self) -> None:
        """Force reload of catalog and clear transformation cache."""
        self._catalog = None
        self._transformation_cache.clear()


# ═══════════════════════════════════════════════════════════════════════════════
# SINGLETON INSTANCE
# ═══════════════════════════════════════════════════════════════════════════════

_provider: CatalogTransformationProvider | None = None


def get_transformation_provider() -> CatalogTransformationProvider:
    """Get singleton transformation provider."""
    global _provider
    if _provider is None:
        _provider = CatalogTransformationProvider()
    return _provider


def get_catalog_transformation(
    model_id: str, model_metadata: dict[str, Any] | None = None
) -> dict[str, Any] | None:
    """
    Get transformation for model using catalog-aware lookup.

    This is the recommended entry point for transformation lookup.
    Uses catalog as primary source with legacy fallback.

    Args:
        model_id: Model identifier
        model_metadata: Optional model metadata from Gateway

    Returns:
        Transformation configuration, or None if no transformation needed
    """
    return get_transformation_provider().get_transformation_for_model(
        model_id, model_metadata
    )


def set_catalog_from_gateway(catalog: dict[str, Any]) -> None:
    """
    Set catalog data received from Gateway.

    Call this when Stargate receives catalog data from Gateway's metadata endpoint.

    Args:
        catalog: Catalog dictionary from Gateway
    """
    get_transformation_provider().set_gateway_catalog(catalog)


async def fetch_catalog_from_gateway(gateway_client) -> bool:
    """
    Fetch catalog transformations from Gateway and cache them.

    This should be called during Stargate startup after gateway connection
    is established.

    Args:
        gateway_client: Connected GatewayClient instance

    Returns:
        True if fetch succeeded, False otherwise
    """
    try:
        catalog = await gateway_client.get_catalog(include_models=False)
        if catalog:
            set_catalog_from_gateway(catalog)
            transformations = catalog.get("transformations", {})
            logger.info(
                f"Fetched {len(transformations)} transformations from Gateway catalog"
            )
            return True
        else:
            logger.warning("Gateway catalog returned empty response")
            return False
    except Exception as e:
        logger.warning(f"Failed to fetch catalog from Gateway: {e}")
        return False
