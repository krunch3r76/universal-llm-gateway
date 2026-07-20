"""Model metadata adapter package for API-friendly registry formatting.

Package-shadow of model_metadata_adapter.py. Re-exports ModelMetadataAdapter and
extract_comprehensive_model_info so existing router and lifecycle import paths
remain unchanged.
"""

from .adapter import ModelMetadataAdapter
from .model_info_extract import extract_comprehensive_model_info
from .text_normalization import safe_lower

__all__ = [
    "ModelMetadataAdapter",
    "extract_comprehensive_model_info",
    "safe_lower",
]
