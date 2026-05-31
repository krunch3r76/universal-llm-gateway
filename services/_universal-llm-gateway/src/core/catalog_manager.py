"""
Catalog Manager - Write operations for model catalogs.

Write Targets (V3 dual-write):
    - Static:  config/models/<domain>/<engine>/<model_id>.yaml  (metadata-only)
    - Local:   $GATEWAY_LOCAL_CATALOG_DIR/<domain>/<engine>/<model_id>.yaml (full entry)

Both targets are always written on every upsert.

Local Directory:
    GATEWAY_LOCAL_CATALOG_DIR env var points directly to domain root.
    Default: ~/.gateway/catalog/
    Structure: $GATEWAY_LOCAL_CATALOG_DIR/text_llm/llama-cpp/*.yaml

Domain-based structure matches CatalogLoader discovery pattern.
Uses catalog.split utilities for path mapping and file writing.
"""

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from universal_logging import get_logger

from .catalog import CatalogLoader, get_catalog_loader
from .catalog.constants import CATALOG_SCHEMA_VERSION, CATALOG_STATIC_SCHEMA_VERSION
from .catalog.schemas import SchemaRegistry
from .catalog.split import determine_model_path, write_model_file
from .file_locker import FileLock

# Loader fields retained in static entries — routing discriminants, not operational params.
# ∀ k ∈ _LOADER_STATIC_KEYS: present in static entry so gateway can dispatch correctly
# without loading the full local entry (e.g., embedding detection for measurement jobs).
_LOADER_STATIC_KEYS = frozenset(
    {"embedding", "embedding_task_default", "clip_model_path", "vision_architecture"}
)

logger = get_logger(__name__)

_METADATA_STRIP_KEYS = frozenset({"activated_gpu_contexts", "activated_cpu_contexts"})
_ENTRY_STRIP_SECTIONS = frozenset({"loader", "devices"})


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
        - Dual-write: every upsert writes static (metadata-only) + local (full entry)
        - Validates V2/V3 schema compliance (fail-fast on V1)
        - Atomic writes with per-file locking
    """

    def __init__(self, catalog_loader: CatalogLoader | None = None) -> None:
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
    def local_catalog_dir(self) -> Path:
        """
        Local catalog directory for full operational entries.

        Resolution:
            1. GATEWAY_LOCAL_CATALOG_DIR env var (production/Docker)
            2. ~/.gateway/catalog/ (default)

        Raises:
            CatalogValidationError: If GATEWAY_LOCAL_CATALOG_DIR is set but invalid
        """
        explicit = os.getenv("GATEWAY_LOCAL_CATALOG_DIR")
        if explicit:
            path = Path(explicit)
            if path.exists() and not path.is_dir():
                raise CatalogValidationError(
                    f"GATEWAY_LOCAL_CATALOG_DIR points to non-directory: {path}"
                )
            return path

        return Path.home() / ".gateway" / "catalog"

    def _strip_measurement_data(self, entry: dict[str, Any]) -> dict[str, Any]:
        """
        Produce static entry from full entry.

        Strips devices, operational loader keys, and activated_*_contexts from metadata.
        Preserves routing-only loader fields (embedding, embedding_task_default, etc.)
        so the gateway can dispatch correctly without loading the full local entry.

        ∀ static_entry: catalog_schema = CATALOG_STATIC_SCHEMA_VERSION (4)
        ∀ static_entry: ¬devices ∧ ¬activated_*_contexts ∧ loader ⊆ _LOADER_STATIC_KEYS
        """
        metadata = {
            k: v
            for k, v in entry.get("metadata", {}).items()
            if k not in _METADATA_STRIP_KEYS
        }

        static: dict[str, Any] = {"catalog_schema": CATALOG_STATIC_SCHEMA_VERSION}
        static["schema"] = entry["schema"]
        static["metadata"] = metadata

        loader_static = {
            k: v for k, v in entry.get("loader", {}).items() if k in _LOADER_STATIC_KEYS
        }
        if loader_static:
            static["loader"] = loader_static

        if "download" in entry:
            static["download"] = entry["download"]
        return static

    def _ensure_local_entry(self, entry: dict[str, Any]) -> dict[str, Any]:
        """
        Ensure local entry has catalog_schema: CATALOG_SCHEMA_VERSION (3) as first key.

        ∀ local_entry: catalog_schema = 3 ∧ devices present (enforcement is caller's responsibility)
        """
        if entry.get("catalog_schema") == CATALOG_SCHEMA_VERSION:
            return entry
        result: dict[str, Any] = {"catalog_schema": CATALOG_SCHEMA_VERSION}
        for k, v in entry.items():
            if k != "catalog_schema":
                result[k] = v
        return result

    def _get_model_file_path(
        self, model_id: str, entry: dict[str, Any], *, base_dir: Path
    ) -> Path:
        """Determine file path for model using catalog.split.mapping."""
        domain_engine = determine_model_path(model_id, entry)
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
            write_model_file(path, entry)
            logger.debug(f"Wrote model file: {path}")

    def _validate_entry(self, model_id: str, entry: dict[str, Any]) -> None:
        """
        Validate entry (V2/V3 compliant, fail-fast on V1).

        Checks:
            - Required fields: schema, metadata
            - No V1 keys: configurations, base_loader
            - Schema exists in registry

        Args:
            model_id: Model identifier
            entry: Model entry dict

        Raises:
            CatalogValidationError: If validation fails
        """
        if "schema" not in entry:
            raise CatalogValidationError(f"Model '{model_id}' missing 'schema' field")
        if "metadata" not in entry:
            raise CatalogValidationError(f"Model '{model_id}' missing 'metadata' field")

        if "configurations" in entry:
            raise CatalogValidationError(
                f"Model '{model_id}' uses V1 'configurations' key "
                "- use 'devices' instead"
            )
        if "base_loader" in entry:
            raise CatalogValidationError(
                f"Model '{model_id}' uses V1 'base_loader' key - use 'loader' instead"
            )

        schema = SchemaRegistry.get_for_model(entry)
        if not schema:
            raise CatalogValidationError(
                f"Model '{model_id}' has unknown schema '{entry.get('schema')}'"
            )

        # Only run full schema validation when entry has devices (local entry)
        if "devices" in entry:
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
    ) -> CatalogOperationResult:
        """
        Insert or update model — dual-write to static and local catalogs.

        Static write: metadata-only (stripped of loader, devices, activated_*_contexts)
        Local write: full entry with loader + devices

        Args:
            model_id: Model identifier
            entry: Full model entry dict (V2/V3 format required)
            allow_overwrite: Allow overwriting existing model

        Returns:
            CatalogOperationResult with local file_path

        Raises:
            CatalogValidationError: If validation fails or overwrite denied
        """
        self._validate_entry(model_id, entry)

        local_entry = self._ensure_local_entry(entry)
        static_entry = self._strip_measurement_data(entry)

        local_path = self._get_model_file_path(
            model_id, entry, base_dir=self.local_catalog_dir
        )
        static_path = self._get_model_file_path(
            model_id, entry, base_dir=self.static_models_dir
        )

        existing = local_path.exists() or static_path.exists()

        if existing and not allow_overwrite:
            raise CatalogValidationError(
                f"Model '{model_id}' already exists in catalog at {local_path}"
            )

        self._write_model_file_atomic(local_path, local_entry)
        self._write_model_file_atomic(static_path, static_entry)
        self._loader.reload()

        operation: Literal["created", "updated"] = "updated" if existing else "created"
        logger.info(
            f"{operation.title()} model '{model_id}': "
            f"local={local_path}, static={static_path}"
        )

        return CatalogOperationResult(
            operation=operation, model_id=model_id, file_path=local_path
        )

    def upsert_local_only(
        self,
        model_id: str,
        entry: dict[str, Any],
    ) -> CatalogOperationResult:
        """
        Write full entry to local catalog only — no static write.

        Used by gateway-side measurement to persist device profiles without
        touching the static catalog (which is version-controlled on the host).

        ∀ caller: entry must include devices section before calling.

        Args:
            model_id: Model identifier
            entry: Full model entry dict (must include devices)

        Returns:
            CatalogOperationResult with local file_path
        """
        self._validate_entry(model_id, entry)

        local_entry = self._ensure_local_entry(entry)
        local_path = self._get_model_file_path(
            model_id, entry, base_dir=self.local_catalog_dir
        )

        existing = local_path.exists()
        self._write_model_file_atomic(local_path, local_entry)
        self._loader.reload()

        operation: Literal["created", "updated"] = "updated" if existing else "created"
        logger.info(
            f"{operation.title()} local-only catalog entry for '{model_id}': {local_path}"
        )

        return CatalogOperationResult(
            operation=operation, model_id=model_id, file_path=local_path
        )

    def delete_model(self, model_id: str) -> CatalogOperationResult:
        """
        Delete model files from both local and static catalogs.

        Args:
            model_id: Model identifier

        Returns:
            CatalogOperationResult

        Raises:
            CatalogValidationError: If model not found in either catalog
        """
        local_file: Path | None = None
        static_file: Path | None = None

        if self.local_catalog_dir.exists():
            for yaml_file in self.local_catalog_dir.rglob(f"{model_id}.yaml"):
                local_file = yaml_file
                break

        for yaml_file in self.static_models_dir.rglob(f"{model_id}.yaml"):
            static_file = yaml_file
            break

        if not local_file and not static_file:
            raise CatalogValidationError(f"Model '{model_id}' not found in catalog")

        deleted_paths: list[Path] = []
        if local_file and local_file.exists():
            local_file.unlink()
            logger.info(f"Deleted local catalog entry: {local_file}")
            deleted_paths.append(local_file)

        if static_file and static_file.exists():
            static_file.unlink()
            logger.info(f"Deleted static catalog entry: {static_file}")
            deleted_paths.append(static_file)

        if not deleted_paths:
            raise CatalogValidationError(f"Model '{model_id}' not found in catalog")

        self._loader.reload()

        return CatalogOperationResult(
            operation="deleted",
            model_id=model_id,
            file_path=deleted_paths[0] if len(deleted_paths) == 1 else None,
            message=f"Deleted model '{model_id}' from catalog ({len(deleted_paths)} file(s))",
        )


# Singleton instance
_default_manager: CatalogManager | None = None


def get_catalog_manager() -> CatalogManager:
    """Get the catalog manager singleton instance."""
    global _default_manager
    if _default_manager is None:
        _default_manager = CatalogManager()
    return _default_manager
