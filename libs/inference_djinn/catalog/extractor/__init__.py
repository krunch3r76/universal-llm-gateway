"""
Metadata extraction for model catalog entries.

Public API:
    - CatalogMetadata: Dataclass for extracted metadata
    - MetadataExtractor: Main extraction interface
"""

from .base import CatalogMetadata, MetadataExtractor
from .exl3 import extract_exl3
from .gguf import extract_gguf
from .hf import extract_hf

__all__ = [
    "CatalogMetadata",
    "MetadataExtractor",
    "extract_gguf",
    "extract_hf",
    "extract_exl3",
]
