"""
Common/shared utilities subpackage.

Contains cross-cutting helpers used by both streaming and non-streaming pipelines.
"""

from .chunk_processor import ChunkProcessor
from .error_formats import format_anthropic, format_custom, format_google, format_openai
from .error_normalizer import ErrorNormalizer
from .error_types import ErrorFormat, determine_error_type, determine_status_code
from .gateway_error_interceptor import GatewayErrorInterceptor
from .resource_manager import GatewayResourceManager

# Remove import - truncation now automatic

__all__ = [
    # Core classes
    "ChunkProcessor",
    "ErrorNormalizer",
    "GatewayErrorInterceptor",
    "GatewayResourceManager",
    # Error types
    "ErrorFormat",
    "determine_error_type",
    "determine_status_code",
    # Error formatters
    "format_anthropic",
    "format_custom",
    "format_google",
    "format_openai",
    # Utilities (truncate_for_logging removed - now automatic)
]
