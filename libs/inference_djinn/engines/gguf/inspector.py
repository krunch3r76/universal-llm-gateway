"""Public GGUF inspector façade.

Re-exports the stable GGUF inspection surface used by service code and
LazyInspector while implementation lives in focused sibling modules.
"""

from .chat_template_analysis import (
    detect_chat_template_support,
    extract_chat_template_ascii,
    gguf_parser_available,
)
from .config_recommendations import generate_recommended_config
from .metadata_loading import (
    clear_metadata_cache,
    gguf_reader_available,
    load_gguf_metadata,
)
from .model_capabilities import analyze_model_capabilities
from .model_requirements import validate_model_requirements
from .model_summary import get_model_info_summary
from .model_type_detection import detect_model_type_from_metadata

__all__ = [
    "load_gguf_metadata",
    "clear_metadata_cache",
    "detect_model_type_from_metadata",
    "detect_chat_template_support",
    "extract_chat_template_ascii",
    "analyze_model_capabilities",
    "generate_recommended_config",
    "validate_model_requirements",
    "get_model_info_summary",
    "gguf_parser_available",
    "gguf_reader_available",
]
