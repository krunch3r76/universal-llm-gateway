"""Model registry package for managing model metadata and validation"""

# src/core/model_registry/
# ├── __init__.py              # Package exports
# ├── registry.py              # Main ModelRegistry class
# ├── metadata.py              # ModelMetadata dataclass and extraction logic
# ├── validation.py            # Model validation logic
# └── extractors/              # Format-specific metadata extractors
#     ├── __init__.py
#     ├── base.py              # Base extractor interface
#     ├── gguf.py              # GGUF metadata extraction
#     ├── transformers.py      # AWQ/GPTQ metadata extraction
from .metadata import ModelMetadata
from .registry import ModelRegistry

__all__ = ["ModelRegistry", "ModelMetadata"]
