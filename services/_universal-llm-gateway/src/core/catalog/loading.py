"""
Catalog Loading - V3 Schema with Static + Local Catalog Merge.

Architecture:
    - Static catalog: config/models/<domain>/<engine>/*.yaml
      Metadata-only (no loader/devices). Version-controlled.
    - Local catalog: $GATEWAY_LOCAL_CATALOG_DIR or ~/.gateway/catalog/
      Full operational entries (metadata + loader + devices). Per-install.

Merge Strategy:
    - Local entry exists → use local (full operational copy with profiles)
    - Only static exists → use static (unmeasured, no profiles yet)
    - Fail-fast on static catalog errors; warn+skip on local catalog errors
"""

import os
from pathlib import Path
from typing import Any

import yaml
from universal_logging import get_logger

from .constants import CATALOG_SCHEMA_VERSION
from .local import ALLOWED_DOMAINS, discover_local_models, get_local_catalog_dir

logger = get_logger(__name__)


class CatalogDiscovery:
    """
    Discover and load catalog models from domain-based directory structure.

    Responsibilities:
        - Discover individual model files in domain directories
        - Validate model ID uniqueness
        - Fail-fast on invalid structure
    """

    def __init__(self, catalog_dir: Path) -> None:
        self.catalog_dir = catalog_dir
        self.models_dir = catalog_dir / "models"

    def discover_model_files(self) -> dict[str, Path]:
        """
        Discover all model YAML files in domain directories.

        Returns:
            Dict mapping model_id -> file_path

        Raises:
            ValueError: If models directory missing, empty, or contains invalid files
        """
        model_files: dict[str, Path] = {}

        if not self.models_dir.exists():
            raise ValueError(
                f"Models directory not found: {self.models_dir}\n"
                f"Expected domain-based catalog at config/models/"
            )

        for yaml_file in self.models_dir.rglob("*.yaml"):
            try:
                rel = yaml_file.relative_to(self.models_dir)
            except ValueError:
                continue

            if not rel.parts:
                continue

            domain = rel.parts[0]
            if domain not in ALLOWED_DOMAINS:
                raise ValueError(
                    f"Invalid catalog file location: {yaml_file}\n"
                    f"Expected domain to be one of: {sorted(ALLOWED_DOMAINS)}\n"
                    f"Got: {domain}"
                )

            model_id = yaml_file.stem

            if model_id in model_files:
                raise ValueError(
                    f"Duplicate model ID '{model_id}' found:\n"
                    f"  - {model_files[model_id]}\n"
                    f"  - {yaml_file}\n"
                    f"Model IDs must be unique across all catalog files."
                )

            model_files[model_id] = yaml_file

        if not model_files:
            raise ValueError(
                f"No model files found in {self.models_dir}\n"
                f"Expected *.yaml files under domain directories."
            )

        logger.info(f"Discovered {len(model_files)} model files in {self.models_dir}")
        return model_files

    def load_model_from_file(self, model_id: str, file_path: Path) -> dict[str, Any]:
        """
        Load model entry from individual YAML file.

        Raises:
            ValueError: If file is invalid or missing required fields
        """
        try:
            with open(file_path) as f:
                model_entry = yaml.safe_load(f)

            if not model_entry:
                raise ValueError(f"Empty model file: {file_path}")

            if "schema" not in model_entry:
                raise ValueError(f"Missing 'schema' field in {file_path}")

            if "metadata" not in model_entry:
                raise ValueError(f"Missing 'metadata' field in {file_path}")

            return model_entry

        except yaml.YAMLError as e:
            raise ValueError(f"Invalid YAML in {file_path}: {e}") from e

    def load_catalog(self) -> dict[str, Any]:
        """
        Load catalog from domain-based directory structure.

        Returns:
            Catalog dict with V3 schema

        Raises:
            ValueError: If discovery fails or validation errors
        """
        model_files = self.discover_model_files()

        models: dict[str, Any] = {}
        for model_id, file_path in model_files.items():
            model_entry = self.load_model_from_file(model_id, file_path)
            models[model_id] = model_entry

        catalog = {
            "schema_version": CATALOG_SCHEMA_VERSION,
            "catalog_version": "1.0",
            "catalog_type": "static",
            "models": models,
        }

        logger.info(f"Loaded catalog: {len(models)} models from {self.models_dir}")
        return catalog


class CatalogLoader:
    """
    Unified catalog loader with static + local merge.

    Sources (in precedence order):
        1. Local: $GATEWAY_LOCAL_CATALOG_DIR or ~/.gateway/catalog/ (full entries)
        2. Static: config/models/ (metadata-only, version-controlled)

    Merge Strategy:
        - Local entry exists → use local (full operational copy with profiles)
        - Only static exists → use static (unmeasured, no profiles yet)
        - Invalid local files are skipped with warning (not fatal)
    """

    def __init__(self, catalog_dir: Path | None = None) -> None:
        if catalog_dir is None:
            catalog_dir = self._detect_catalog_dir()

        self.catalog_dir = Path(catalog_dir)
        self.static_discovery = CatalogDiscovery(self.catalog_dir)
        self._catalog_cache: dict[str, Any] | None = None

    def _detect_catalog_dir(self) -> Path:
        """
        Detect catalog directory from workspace root.

        Docker/Production (WORKSPACE_ROOT set):
            Fail fast if config/models not found (container misconfiguration).

        Development (WORKSPACE_ROOT not set):
            Auto-detect from current directory or source file location.
        """
        workspace_root = os.getenv("WORKSPACE_ROOT")
        if workspace_root:
            workspace_path = Path(workspace_root)
            config_path = workspace_path / "config"
            models_path = config_path / "models"

            if not models_path.exists():
                raise ValueError(
                    f"Models directory not found: {models_path}\n"
                    f"WORKSPACE_ROOT is set to {workspace_root}, "
                    f"but config/models does not exist.\n"
                    f"This indicates a container build/configuration error.\n"
                    f"Expected domain-based catalog at config/models/"
                )

            return config_path

        cwd = Path.cwd()
        if (cwd / "config" / "models").exists():
            return cwd / "config"

        # __file__ = /app/services/_universal-llm-gateway/src/core/catalog/loading.py
        # 6 parents = /app
        return Path(__file__).parent.parent.parent.parent.parent.parent / "config"

    @property
    def local_catalog_dir(self) -> Path | None:
        """Local catalog directory (from env var or ~/.gateway/catalog/)."""
        return get_local_catalog_dir()

    def load(self) -> dict[str, Any]:
        """
        Load catalog from static and local sources.

        Merge: Local models override static (same model_id).
        Invalid models are excluded and logged at ERROR — not fatal.

        Returns:
            Catalog with V3 schema, invalid models excluded
        """
        if self._catalog_cache is not None:
            return self._catalog_cache

        static_catalog = self.static_discovery.load_catalog()
        static_models = static_catalog.get("models", {})

        local_models = discover_local_models()

        merged_models = {**static_models, **local_models}

        override_count = len(set(local_models) & set(static_models))
        if local_models:
            logger.info(
                f"Merged catalog: {len(static_models)} static + "
                f"{len(local_models)} local = {len(merged_models)} total "
                f"({override_count} override(s))"
            )

        catalog = {
            "schema_version": CATALOG_SCHEMA_VERSION,
            "catalog_version": "1.0",
            "catalog_type": "merged" if local_models else "static",
            "models": merged_models,
        }

        from .validation import validate_catalog

        issues = validate_catalog(catalog)
        errors = [i for i in issues if i.severity == "error"]

        if errors:
            invalid_ids = {i.model_id for i in errors}
            for issue in errors:
                logger.error(
                    f"Excluding invalid model [{issue.model_id}]: {issue.message}"
                )
            catalog["models"] = {
                mid: entry
                for mid, entry in merged_models.items()
                if mid not in invalid_ids
            }
            logger.error(
                f"Excluded {len(invalid_ids)} invalid model(s) from catalog "
                f"({len(catalog['models'])} remaining)"
            )

        self._catalog_cache = catalog
        return catalog

    def reload(self) -> None:
        """Force reload of catalog from disk (clears cache)."""
        self._catalog_cache = None

    def get_model(self, model_id: str) -> dict[str, Any] | None:
        """Get model entry by ID."""
        catalog = self.load()
        return catalog.get("models", {}).get(model_id)

    def list_models(self) -> list[str]:
        """List all model IDs in catalog."""
        catalog = self.load()
        return list(catalog.get("models", {}).keys())

    def get_model_metadata(self, model_id: str) -> dict[str, Any] | None:
        """Get model metadata by ID."""
        model = self.get_model(model_id)
        return model.get("metadata") if model else None

    def get_model_download(self, model_id: str) -> dict[str, Any] | None:
        """Get model download info by ID."""
        model = self.get_model(model_id)
        return model.get("download") if model else None

    def list_models_by_format(self, format_type: str) -> list[str]:
        """List model IDs filtered by format."""
        catalog = self.load()
        return [
            mid
            for mid, mdata in catalog.get("models", {}).items()
            if mdata.get("metadata", {}).get("format") == format_type
        ]
