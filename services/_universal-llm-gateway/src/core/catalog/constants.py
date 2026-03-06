"""
Catalog Constants - Schema Definitions.

Two-track catalog_schema versioning:
    - CATALOG_SCHEMA_VERSION = 3: local/operational catalog (~/.gateway/catalog/)
    - CATALOG_STATIC_SCHEMA_VERSION = 4: static catalog (config/models/)

Both tracks share the schema-per-engine pattern and per-file catalog_schema field.
Runtime validation uses >= 3 and handles both values correctly.

    - Static catalog (config/models/): metadata-only, version-controlled, catalog_schema: 4
    - Local catalog (~/.gateway/catalog/): full operational entry, per-install, catalog_schema: 3
    - NO backward compatibility (fail fast on V1/V2 patterns)

Invariants:
    ∀ model: model.schema ∈ VALID_SCHEMAS
    ∀ model: model.metadata.format ∈ VALID_FORMATS
    ∀ static_entry: catalog_schema = 4 ∧ ¬devices ∧ ¬activated_*_contexts
                    ∧ loader ⊆ {embedding, embedding_task_default, clip_model_path, vision_architecture}
                    (routing discriminants only — present so gateway dispatches correctly without a full local entry)
    ∀ local_entry: catalog_schema = 3 ∧ devices ≠ ∅ ∧ loader present
    ¬∃ metadata.engine  (removed in V2)
    ¬∃ configurations  (renamed to devices)
"""

# ═══════════════════════════════════════════════════════════════════════════════
# SCHEMA VERSION
# ═══════════════════════════════════════════════════════════════════════════════

# Local/operational catalog (~/.gateway/catalog/) — full entries with devices + loader
CATALOG_SCHEMA_VERSION = 3

# Static catalog (config/models/) — metadata-only, version-controlled
CATALOG_STATIC_SCHEMA_VERSION = 4

# Per-file schema version key (present in every catalog YAML file)
CATALOG_FILE_SCHEMA_KEY = "catalog_schema"

# ═══════════════════════════════════════════════════════════════════════════════
# VALID SCHEMAS (Engine Names)
# ═══════════════════════════════════════════════════════════════════════════════

VALID_SCHEMAS = frozenset(
    {
        "llama-cpp",
        "vllm",
        "exllamav3",
        "faster-whisper",
        "diffusers",
        "ctranslate2",
    }
)

# ═══════════════════════════════════════════════════════════════════════════════
# VALID FORMATS
# ═══════════════════════════════════════════════════════════════════════════════

VALID_FORMATS = frozenset(
    {
        "gguf",
        "hf",
        "awq",
        "gptq",
        "exl3",
        "whisper",
        "flux2",
        "ct2",
    }
)

# Format to schema mapping (REFERENCE ONLY - V2 requires explicit schema field)
# Used during migration; NOT used for fallback in validation/conversion
FORMAT_TO_SCHEMA = {
    "gguf": "llama-cpp",
    "hf": "vllm",
    "awq": "vllm",
    "gptq": "vllm",
    "exl3": "exllamav3",
    "whisper": "faster-whisper",
    "flux2": "diffusers",
    "ct2": "ctranslate2",
}

# ═══════════════════════════════════════════════════════════════════════════════
# DEVICE TYPES
# ═══════════════════════════════════════════════════════════════════════════════

# All valid device types
VALID_DEVICES = frozenset({"gpu", "cpu", "hybrid"})

# Device type constants
GPU_DEVICE = "gpu"
CPU_DEVICE = "cpu"
HYBRID_DEVICE = "hybrid"

# ═══════════════════════════════════════════════════════════════════════════════
# RESERVED KEYS
# ═══════════════════════════════════════════════════════════════════════════════

# Reserved keys at model entry level (not device names)
RESERVED_MODEL_KEYS = frozenset(
    {"catalog_schema", "schema", "metadata", "loader", "devices", "download"}
)
