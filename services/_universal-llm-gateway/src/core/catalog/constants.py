"""
Catalog Constants - V3 Schema Definitions.

V3 Schema:
    - Schema-per-engine pattern
    - Static catalog (config/models/): metadata-only, version-controlled
    - Local catalog (~/.gateway/catalog/): full operational entry, per-install
    - catalog_schema: 3 in every file (per-file field, not catalog-level)
    - NO backward compatibility (fail fast on V1 patterns)

Invariants:
    ∀ model: model.schema ∈ VALID_SCHEMAS
    ∀ model: model.metadata.format ∈ VALID_FORMATS
    ∀ static_entry: ¬loader ∧ ¬devices ∧ ¬activated_*_contexts
    ∀ local_entry: full operational entry (metadata + loader + devices)
    ¬∃ metadata.engine  (removed in V2)
    ¬∃ configurations  (renamed to devices)
"""

# ═══════════════════════════════════════════════════════════════════════════════
# SCHEMA VERSION
# ═══════════════════════════════════════════════════════════════════════════════

CATALOG_SCHEMA_VERSION = 3

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
