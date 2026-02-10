"""
Catalog Manager - Write operations for model catalogs.

Write Targets:
    - Static: config/models/<domain>/<engine>/<model_id>.yaml (maintainer mode)
    - Dynamic: $GATEWAY_DYNAMIC_CATALOG_DIR/<domain>/<engine>/<model_id>.yaml (user mode)

Dynamic Directory:
    GATEWAY_DYNAMIC_CATALOG_DIR env var points directly to domain root.
    Structure: $GATEWAY_DYNAMIC_CATALOG_DIR/text_llm/llama-cpp/*.yaml
    
    Fallback: config/models-dynamic/ for local development.

Domain-based structure matches CatalogLoader discovery pattern.
Uses catalog.split utilities for path mapping and file writing.
"""

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from universal_logging import get_logger

from .catalog import CatalogLoader, get_catalog_loader
from .catalog.schemas import SchemaRegistry
from .catalog.split import determine_model_path, write_model_file
from .file_locker import FileLock

logger = get_logger(__name__)


class CatalogValidationError(Exception):
    """Raised when catalog validation fails."""

    pass


@dataclass
class CatalogOperationResult:
    """Result of a catalog operation."""

    operation: Literal["created", "updated", "deleted"]
    model_id: str
    file_path: Path | None = None
    message: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for API responses."""
        return {
            "status": "success",
            "model_id": self.model_id,
            "operation": self.operation,
            "file_path": str(self.file_path) if self.file_path else None,
            "message": self.message or f"Model '{self.model_id}' {self.operation}",
        }


class CatalogManager:
    """
    Manages write operations to model catalogs.

    Architecture:
        - Writes individual model files (domain-based structure)
        - Validates V2 schema compliance (fail-fast on V1)
        - Atomic writes with per-file locking
        - Supports static (maintainer) and dynamic (user) targets
    """

    def __init__(self, catalog_loader: CatalogLoader | None = None):
        """
        Initialize catalog manager.

        Args:
            catalog_loader: CatalogLoader instance (uses singleton if None)
        """
        self._loader = catalog_loader or get_catalog_loader()

    @property
    def static_models_dir(self) -> Path:
        """Static catalog models directory (config/models/)."""
        return self._loader.catalog_dir / "models"

    @property
    def dynamic_models_dir(self) -> Path:
        """
        Dynamic catalog models directory.

        Simplified: Uses GATEWAY_DYNAMIC_CATALOG_DIR env var only.
        Docker should mount a local volume to this path.

        Returns config/models-dynamic/ as fallback for local dev.

        Raises:
            CatalogValidationError: If GATEWAY_DYNAMIC_CATALOG_DIR is set but invalid
        """
        explicit = os.getenv("GATEWAY_DYNAMIC_CATALOG_DIR")
        if explicit:
            path = Path(explicit)
            # Fail-fast on invalid env var (operational misconfiguration)
            if path.exists() and not path.is_dir():
                raise CatalogValidationError(
                    f"GATEWAY_DYNAMIC_CATALOG_DIR points to non-directory: {path}"
                )
            return path

        # Local dev fallback (not used in production containers)
        return self._loader.catalog_dir / "models-dynamic"

    def _get_model_file_path(self, model_id: str, entry: dict[str, Any], static: bool) -> Path:
        """
        Determine file path for model using catalog.split.mapping.

        Args:
            model_id: Model identifier
            entry: Model entry dict (must have schema field)
            static: Write to static (True) or dynamic (False) catalog

        Returns:
            Full path to model YAML file
        """
        domain_engine = determine_model_path(model_id, entry)
        base_dir = self.static_models_dir if static else self.dynamic_models_dir
        return base_dir / domain_engine / f"{model_id}.yaml"

    def _write_model_file_atomic(self, path: Path, entry: dict[str, Any]) -> None:
        """
        Write model entry with atomic write and file locking.

        Uses:
            - Per-file lock (path.with_suffix('.yaml.lock'))
            - Atomic write (temp file + os.replace)
            - catalog.split.writer for YAML formatting

        Args:
            path: Target file path
            entry: Model entry dict

        Raises:
            OSError: If write fails
        """
        lock_path = path.with_suffix(".yaml.lock")
        path.parent.mkdir(parents=True, exist_ok=True)

        with FileLock(lock_path, timeout=30.0):
            # Use split.writer for consistent YAML formatting
            write_model_file(path, entry)
            logger.debug(f"Wrote model file: {path}")

    def _validate_entry_v2(self, model_id: str, entry: dict[str, Any]) -> None:
        """
        Validate entry is V2 compliant (fail-fast, no V1 support).

        Checks:
            - Required fields: schema, metadata
            - No V1 keys: configurations, base_loader
            - Schema exists in registry
            - Schema-specific validation passes

        Args:
            model_id: Model identifier
            entry: Model entry dict

        Raises:
            CatalogValidationError: If validation fails
        """
        # Required fields
        if "schema" not in entry:
            raise CatalogValidationError(f"Model '{model_id}' missing 'schema' field")
        if "metadata" not in entry:
            raise CatalogValidationError(f"Model '{model_id}' missing 'metadata' field")

        # Reject V1 patterns (fail-fast)
        if "configurations" in entry:
            raise CatalogValidationError(
                f"Model '{model_id}' uses V1 'configurations' key - use 'devices' instead"
            )
        if "base_loader" in entry:
            raise CatalogValidationError(
                f"Model '{model_id}' uses V1 'base_loader' key - use 'loader' instead"
            )

        # Validate schema exists
        schema = SchemaRegistry.get_for_model(entry)
        if not schema:
            raise CatalogValidationError(
                f"Model '{model_id}' has unknown schema '{entry.get('schema')}'"
            )

        # Run schema-specific validation
        issues = schema.validate(model_id, entry)
        errors = [i for i in issues if i.severity == "error"]
        if errors:
            raise CatalogValidationError(
                f"Model '{model_id}' validation failed: {errors[0].message}"
            )

    def upsert_model(
        self,
        model_id: str,
        entry: dict[str, Any],
        *,
        allow_overwrite: bool = True,
        static: bool = False,
    ) -> CatalogOperationResult:
        """
        Insert or update model to individual file.

        Args:
            model_id: Model identifier
            entry: Model entry dict (V2 format required)
            allow_overwrite: Allow overwriting existing model
            static: Write to static catalog (maintainer mode)

        Returns:
            CatalogOperationResult with operation details

        Raises:
            CatalogValidationError: If validation fails or overwrite denied
        """
        self._validate_entry_v2(model_id, entry)

        file_path = self._get_model_file_path(model_id, entry, static)
        existing = file_path.exists()

        if existing and not allow_overwrite:
            catalog_type = "static" if static else "dynamic"
            raise CatalogValidationError(
                f"Model '{model_id}' already exists in {catalog_type} catalog at {file_path}"
            )

        self._write_model_file_atomic(file_path, entry)
        self._loader.reload()

        operation: Literal["created", "updated"] = "updated" if existing else "created"
        logger.info(f"{operation.title()} model '{model_id}' at {file_path}")

        return CatalogOperationResult(
            operation=operation, model_id=model_id, file_path=file_path
        )

    def delete_model(self, model_id: str, *, static: bool = False) -> CatalogOperationResult:
        """
        Delete model file.

        Args:
            model_id: Model identifier
            static: Delete from static catalog (maintainer mode)

        Returns:
            CatalogOperationResult

        Raises:
            CatalogValidationError: If model not found
        """
        base_dir = self.static_models_dir if static else self.dynamic_models_dir
        catalog_type = "static" if static else "dynamic"

        # Search for model file (need to search since we don't know the domain/engine)
        model_file = None
        for yaml_file in base_dir.rglob(f"{model_id}.yaml"):
            model_file = yaml_file
            break

        if not model_file or not model_file.exists():
            raise CatalogValidationError(
                f"Model '{model_id}' not found in {catalog_type} catalog"
            )

        model_file.unlink()
        self._loader.reload()

        logger.info(f"Deleted model '{model_id}' from {model_file}")

        return CatalogOperationResult(
            operation="deleted",
            model_id=model_id,
            file_path=model_file,
            message=f"Deleted model '{model_id}' from {catalog_type} catalog",
        )


# Singleton instance
_default_manager: CatalogManager | None = None


def get_catalog_manager() -> CatalogManager:
    """Get the catalog manager singleton instance."""
    global _default_manager
    if _default_manager is None:
        _default_manager = CatalogManager()
    return _default_manager
