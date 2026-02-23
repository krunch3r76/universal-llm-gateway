"""
Catalog module for inference_djinn.

Provides tooling for:
- Model discovery: Scan directories for uncataloged models
- Metadata extraction: Extract architecture, quantization, context length from model files
- Source tracing: Find HuggingFace origins for local models
- Catalog generation: Generate complete catalog entries
- Schema versioning: Version validation for dynamic configs
- Local config management: Export/manage local dynamic catalog
"""

from .discovery import DiscoveredModel, ModelDiscovery, ModelFormat
from .embedding import infer_embedding_loader, is_embedding_model
from .extractor import CatalogMetadata, MetadataExtractor
from .generator import CatalogEntryGenerator
from .local_config import (
    CatalogConfigError,
    SchemaVersionError,
    add_model_to_local,
    export_model_to_local,
    get_local_catalog_path,
    get_user_config_dir,
    list_local_models,
    load_local_catalog,
    load_static_catalog,
    merge_catalogs,
    remove_model_from_local,
    save_local_catalog,
)
from .schema import (
    CURRENT_SCHEMA_VERSIONS,
    ConfigType,
    SchemaValidationResult,
    get_catalog_template,
    get_current_version,
    validate_catalog_schema,
)
from .tracer import HFSource, SourceTracer

__all__ = [
    "ModelDiscovery",
    "DiscoveredModel",
    "ModelFormat",
    "MetadataExtractor",
    "CatalogMetadata",
    "SourceTracer",
    "HFSource",
    "CatalogEntryGenerator",
    # Embedding detection
    "infer_embedding_loader",
    "is_embedding_model",
    # Schema versioning
    "ConfigType",
    "CURRENT_SCHEMA_VERSIONS",
    "SchemaValidationResult",
    "validate_catalog_schema",
    "get_current_version",
    "get_catalog_template",
    # Local config management
    "CatalogConfigError",
    "SchemaVersionError",
    "get_user_config_dir",
    "get_local_catalog_path",
    "load_local_catalog",
    "save_local_catalog",
    "load_static_catalog",
    "export_model_to_local",
    "add_model_to_local",
    "remove_model_from_local",
    "list_local_models",
    "merge_catalogs",
]
