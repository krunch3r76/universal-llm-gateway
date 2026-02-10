"""
Catalog Loading - V2 Schema with Domain-Based Discovery.

Post-Cutover Architecture:
    - Static catalog: config/models/<domain>/<engine>/*.yaml (baseline)
    - Dynamic catalog: $GATEWAY_DYNAMIC_CATALOG_DIR/<domain>/<engine>/*.yaml (user overrides, optional)
    - Merge: Dynamic models override static models with same model_id
    - Fail-fast on static catalog errors; warn+skip on dynamic catalog errors

Discovery:
    - Recursively scans config/models/ for *.yaml files
    - Only loads from approved domain roots: text_llm, audio, translation, visual, graphics
    - Filename stem = model ID (strict convention)
    - Validates model ID uniqueness

Dynamic Directory Contract:
    GATEWAY_DYNAMIC_CATALOG_DIR points to domain root (NOT models/ subdirectory).
    Example: $GATEWAY_DYNAMIC_CATALOG_DIR/text_llm/llama-cpp/*.yaml
"""

import os
from pathlib import Path
from typing import Any

import yaml
from universal_logging import get_logger

from .constants import CATALOG_SCHEMA_VERSION

logger = get_logger(__name__)

# Approved domain directories (guardrail against stray YAML files)
ALLOWED_DOMAINS = frozenset({"text_llm", "audio", "translation", "visual", "graphics"})


def _load_dynamic_entry(yaml_file: Path) -> dict[str, Any] | None:
    """
    Load single dynamic model file.

    Returns:
        Model entry dict, or None if invalid (warning logged)
    """
    try:
        with open(yaml_file) as f:
            entry = yaml.safe_load(f)

        if not entry:
            logger.warning(f"Skipping empty dynamic model file: {yaml_file}")
            return None

        # Type safety: entry must be a dict (not list, string, etc.)
        if not isinstance(entry, dict):
            logger.warning(
                f"Skipping dynamic model '{yaml_file.stem}': root must be a dictionary, got {type(entry).__name__}"
            )
            return None

        if "schema" not in entry:
            logger.warning(
                f"Skipping dynamic model '{yaml_file.stem}': missing 'schema' field"
            )
            return None

        if "metadata" not in entry:
            logger.warning(
                f"Skipping dynamic model '{yaml_file.stem}': missing 'metadata' field"
            )
            return None

        return entry

    except yaml.YAMLError as e:
        logger.warning(f"Skipping invalid YAML in dynamic model: {yaml_file}: {e}")
        return None
    except OSError as e:
        logger.warning(f"Skipping unreadable dynamic model: {yaml_file}: {e}")
        return None


def _validate_dynamic_entry(model_id: str, entry: dict[str, Any]) -> bool:
    """
    Validate dynamic model entry against schema.

    Returns:
        True if valid, False if invalid (warning logged)
    """
    from .schemas import SchemaRegistry

    schema = SchemaRegistry.get_for_model(entry)
    if not schema:
        logger.warning(
            f"Skipping dynamic model '{model_id}': unknown schema '{entry.get('schema')}'"
        )
        return False

    issues = schema.validate(model_id, entry)
    errors = [i for i in issues if i.severity == "error"]

    if errors:
        error_msgs = "; ".join(i.message for i in errors[:3])
        logger.warning(
            f"Skipping dynamic model '{model_id}': validation errors: {error_msgs}"
        )
        return False

    return True


class CatalogDiscovery:
    """
    Discover and load catalog models from domain-based directory structure.

    Responsibilities:
        - Discover individual model files in domain directories
        - Validate model ID uniqueness
        - Fail-fast on invalid structure
    """

    def __init__(self, catalog_dir: Path):
        """
        Initialize catalog discovery.

        Args:
            catalog_dir: Path to config directory containing models/ subdirectory
        """
        self.catalog_dir = catalog_dir
        self.models_dir = catalog_dir / "models"

    def discover_model_files(self) -> dict[str, Path]:
        """
        Discover all model YAML files in domain directories.

        Returns:
            Dict mapping model_id -> file_path

        Raises:
            ValueError: If models directory missing, empty, or contains invalid files

        Convention:
            - Filename stem = model ID
            - Example: qwen3-32b-awq.yaml → model ID "qwen3-32b-awq"
        """
        model_files: dict[str, Path] = {}

        if not self.models_dir.exists():
            raise ValueError(
                f"Models directory not found: {self.models_dir}\n"
                f"Expected domain-based catalog at config/models/"
            )

        # Recursively discover *.yaml files
        for yaml_file in self.models_dir.rglob("*.yaml"):
            # Guardrail: only load YAML under approved domain roots
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

            # Check for duplicates
            if model_id in model_files:
                raise ValueError(
                    f"Duplicate model ID '{model_id}' found:\n"
                    f"  - {model_files[model_id]}\n"
                    f"  - {yaml_file}\n"
                    f"Model IDs must be unique across all catalog files."
                )

            model_files[model_id] = yaml_file
            # logger.debug(f"Discovered model file: {yaml_file.relative_to(self.catalog_dir)}")

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

        Args:
            model_id: Model identifier (for error messages)
            file_path: Path to model YAML file

        Returns:
            Model entry dict (V2 schema)

        Raises:
            ValueError: If file is invalid or missing required fields
        """
        try:
            with open(file_path) as f:
                model_entry = yaml.safe_load(f)

            if not model_entry:
                raise ValueError(f"Empty model file: {file_path}")

            # Validate required V2 fields
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
            Catalog dict with V2 schema

        Raises:
            ValueError: If discovery fails or validation errors
        """
        # Discover all model files
        model_files = self.discover_model_files()

        # Load all models
        models: dict[str, Any] = {}
        for model_id, file_path in model_files.items():
            model_entry = self.load_model_from_file(model_id, file_path)
            models[model_id] = model_entry

        # Build catalog structure
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
    Unified catalog loader with domain-based discovery.

    Sources (in precedence order):
        1. Dynamic: $GATEWAY_DYNAMIC_CATALOG_DIR/<domain>/<engine>/*.yaml (user overrides)
        2. Static: config/models/<domain>/<engine>/*.yaml (baseline catalog)

    Dynamic Directory Contract:
        GATEWAY_DYNAMIC_CATALOG_DIR points directly to domain root, NOT a directory
        containing "models/". The structure mirrors static catalog:

        $GATEWAY_DYNAMIC_CATALOG_DIR/
        ├── text_llm/llama-cpp/*.yaml
        ├── audio/whisper/*.yaml
        └── ...

    Merge Strategy:
        - Dynamic models override static models with same model_id
        - No partial merge (entire model entry replaced)
        - Invalid dynamic files are skipped with warning (not fatal)
    """

    def __init__(self, catalog_dir: Path | None = None):
        """
        Initialize catalog loader.

        Args:
            catalog_dir: Path to config directory (auto-detected if None)
        """
        if catalog_dir is None:
            catalog_dir = self._detect_catalog_dir()

        self.catalog_dir = Path(catalog_dir)
        self.static_discovery = CatalogDiscovery(self.catalog_dir)
        self._catalog_cache: dict[str, Any] | None = None

    def _detect_catalog_dir(self) -> Path:
        """
        Detect catalog directory from workspace root.

        Docker/Production (WORKSPACE_ROOT set):
            - WORKSPACE_ROOT is the authoritative source
            - Fail fast if config/models not found (container misconfiguration)

        Development (WORKSPACE_ROOT not set):
            - Auto-detect from current directory or source file location
        """
        # Docker/Production: WORKSPACE_ROOT is authoritative (not a fallback)
        workspace_root = os.getenv("WORKSPACE_ROOT")
        if workspace_root:
            workspace_path = Path(workspace_root)
            config_path = workspace_path / "config"
            models_path = config_path / "models"

            if not models_path.exists():
                raise ValueError(
                    f"Models directory not found: {models_path}\n"
                    f"WORKSPACE_ROOT is set to {workspace_root}, but config/models does not exist.\n"
                    f"This indicates a container build/configuration error.\n"
                    f"Expected domain-based catalog at config/models/"
                )

            return config_path

        # Development: Auto-detection for local runs
        cwd = Path.cwd()
        if (cwd / "config" / "models").exists():
            return cwd / "config"

        # Development fallback: relative to source file (6 levels up to /app)
        # __file__ = /app/services/_universal-llm-gateway/src/core/catalog/loading.py
        # 6 parents = /app
        return Path(__file__).parent.parent.parent.parent.parent.parent / "config"

    @property
    def dynamic_models_dir(self) -> Path | None:
        """
        Dynamic catalog directory (if exists).

        Resolution:
            1. GATEWAY_DYNAMIC_CATALOG_DIR env var (production)
            2. config/models-dynamic/ fallback (local dev)

        Returns:
            Path if directory exists and is a directory, None otherwise
        """
        explicit = os.getenv("GATEWAY_DYNAMIC_CATALOG_DIR")
        if explicit:
            path = Path(explicit)
            if not path.exists():
                logger.debug(f"Dynamic catalog path does not exist: {path}")
                return None
            if not path.is_dir():
                logger.warning(f"Dynamic catalog path is not a directory: {path}")
                return None
            logger.debug(f"Using dynamic catalog: {path}")
            return path

        dev_path = self.catalog_dir / "models-dynamic"
        if dev_path.exists() and dev_path.is_dir():
            logger.debug(f"Using local dev dynamic catalog: {dev_path}")
            return dev_path

        return None

    def _discover_dynamic_models(self) -> dict[str, Any]:
        """
        Discover models from dynamic catalog (if exists).

        Invalid files are skipped with warning (not fatal).
        Schema validation errors are also skipped (warn + continue).

        Returns:
            Dict of model_id -> model_entry (may be empty)
        """
        dynamic_dir = self.dynamic_models_dir
        if not dynamic_dir:
            return {}

        models: dict[str, Any] = {}

        for yaml_file in dynamic_dir.rglob("*.yaml"):
            try:
                rel = yaml_file.relative_to(dynamic_dir)
            except ValueError:
                continue

            if not rel.parts:
                continue

            domain = rel.parts[0]
            if domain not in ALLOWED_DOMAINS:
                logger.warning(
                    f"Skipping dynamic model in invalid domain: {yaml_file} "
                    f"(expected one of {sorted(ALLOWED_DOMAINS)})"
                )
                continue

            model_id = yaml_file.stem

            if model_id in models:
                logger.warning(
                    f"Duplicate model ID '{model_id}' in dynamic catalog, "
                    f"skipping {yaml_file}"
                )
                continue

            # Load and validate entry
            entry = _load_dynamic_entry(yaml_file)
            if entry is None:
                continue  # Warning already logged

            # Schema validation (warn + skip on error)
            if not _validate_dynamic_entry(model_id, entry):
                continue  # Warning already logged

            models[model_id] = entry
            logger.debug(f"Loaded dynamic model: {model_id} from {yaml_file}")

        if models:
            logger.info(f"Discovered {len(models)} dynamic model(s)")

        return models

    def load(self) -> dict[str, Any]:
        """
        Load catalog from static and dynamic sources.

        Merge: Dynamic models override static (same model_id).
        Invalid dynamic files are skipped (not fatal).

        Caching:
            - Result cached in memory for performance
            - Call reload() to force refresh

        Returns:
            Catalog with V2 schema
        """
        if self._catalog_cache is not None:
            return self._catalog_cache

        # Load static catalog (required)
        static_catalog = self.static_discovery.load_catalog()
        static_models = static_catalog.get("models", {})

        # Load dynamic catalog (optional, for user overrides)
        dynamic_models = self._discover_dynamic_models()

        # Merge: dynamic overrides static
        merged_models = {**static_models, **dynamic_models}

        override_count = len(set(dynamic_models) & set(static_models))
        if dynamic_models:
            logger.info(
                f"Merged catalog: {len(static_models)} static + "
                f"{len(dynamic_models)} dynamic = {len(merged_models)} total "
                f"({override_count} override(s))"
            )

        catalog = {
            "schema_version": CATALOG_SCHEMA_VERSION,
            "catalog_version": "1.0",
            "catalog_type": "merged" if dynamic_models else "static",
            "models": merged_models,
        }

        # Validate merged catalog
        from .validation import validate_catalog

        issues = validate_catalog(catalog)
        errors = [i for i in issues if i.severity == "error"]

        if errors:
            error_msgs = [f"[{i.model_id}] {i.message}" for i in errors[:5]]
            error_summary = "\n  ".join(error_msgs)
            if len(errors) > 5:
                error_summary += f"\n  ... and {len(errors) - 5} more errors"

            raise ValueError(
                f"Catalog validation failed with {len(errors)} error(s):\n"
                f"  {error_summary}\n"
                "Fix catalog errors before starting gateway."
            )

        self._catalog_cache = catalog
        return catalog

    def reload(self) -> None:
        """Force reload of catalog from disk (clears cache)."""
        self._catalog_cache = None

    def get_model(self, model_id: str) -> dict[str, Any] | None:
        """
        Get model entry by ID.

        Args:
            model_id: Model identifier

        Returns:
            Model entry dict or None if not found
        """
        catalog = self.load()
        return catalog.get("models", {}).get(model_id)

    def list_models(self) -> list[str]:
        """List all model IDs in catalog."""
        catalog = self.load()
        return list(catalog.get("models", {}).keys())

    def get_model_metadata(self, model_id: str) -> dict[str, Any] | None:
        """
        Get model metadata by ID.

        Args:
            model_id: Model identifier

        Returns:
            Metadata dictionary, or None if model not found
        """
        model = self.get_model(model_id)
        return model.get("metadata") if model else None

    def get_model_download(self, model_id: str) -> dict[str, Any] | None:
        """
        Get model download info by ID.

        Args:
            model_id: Model identifier

        Returns:
            Download dictionary, or None if model not found
        """
        model = self.get_model(model_id)
        return model.get("download") if model else None

    def list_models_by_format(self, format_type: str) -> list[str]:
        """
        List model IDs filtered by format.

        Args:
            format_type: Model format (gguf, awq, hf, gptq)

        Returns:
            List of model ID strings matching format
        """
        catalog = self.load()
        return [
            mid
            for mid, mdata in catalog.get("models", {}).items()
            if mdata.get("metadata", {}).get("format") == format_type
        ]
