"""
JSON formatting utilities for logging.

Provides consistent JSON formatting for log messages with:
- Unicode support (ensure_ascii=False)
- Automatic truncation of large JSON field values
- Preservation of JSON structure

Usage:
    from universal_logging import get_logger, format_json_for_log

    logger = get_logger(__name__)
    logger.info(f"Request: {format_json_for_log(request_dict)}")
"""

import json
from typing import Any

# Default truncation thresholds for JSON field values in logs
# These are more conservative than GUI display since logs are persistent
DEFAULT_FIELD_TRUNCATE_THRESHOLD = 2000  # Truncate string fields larger than this
DEFAULT_FIELD_PREVIEW_HEAD = 1600  # Show first N chars (80%)
DEFAULT_FIELD_PREVIEW_TAIL = 400  # Show last N chars (20%)


def truncate_json_fields(
    obj: Any,
    max_field_size: int = DEFAULT_FIELD_TRUNCATE_THRESHOLD,
    head_chars: int = DEFAULT_FIELD_PREVIEW_HEAD,
    tail_chars: int = DEFAULT_FIELD_PREVIEW_TAIL,
    protected_paths: frozenset[str] | None = None,
    _current_path: str = "",
) -> Any:
    """
    Recursively truncate large string values in JSON-like objects.

    Preserves JSON structure (dicts, lists, nesting) while truncating
    individual string field values that exceed max_field_size.
    Shows head + tail of truncated content with character count indicator.

    Args:
        obj: JSON-like object (dict, list, or primitive)
        max_field_size: Maximum size for string fields before truncation
        head_chars: Number of characters to show from beginning
        tail_chars: Number of characters to show from end
        protected_paths: Set of field paths to exclude from truncation
            (e.g., "error.traceback")
        _current_path: Internal use - tracks current path during recursion

    Returns:
        Modified object with truncated string fields
    """
    if protected_paths is None:
        protected_paths = frozenset()

    if isinstance(obj, dict):
        return {
            k: truncate_json_fields(
                v,
                max_field_size,
                head_chars,
                tail_chars,
                protected_paths,
                f"{_current_path}.{k}" if _current_path else k,
            )
            for k, v in obj.items()
        }
    elif isinstance(obj, list):
        return [
            truncate_json_fields(
                item,
                max_field_size,
                head_chars,
                tail_chars,
                protected_paths,
                f"{_current_path}[]",
            )
            for item in obj
        ]
    elif isinstance(obj, str) and len(obj) > max_field_size:
        # Check if current path is protected
        if _current_path in protected_paths:
            return obj
        # Truncate large string fields
        truncated_size = len(obj) - head_chars - tail_chars
        head = obj[:head_chars]
        tail = obj[-tail_chars:] if tail_chars > 0 else ""
        return f"{head}\n\n... [TRUNCATED {truncated_size:,} chars] ...\n\n{tail}"
    else:
        return obj


def format_json_for_log(
    data: Any,
    indent: int = 2,
    truncate: bool = True,
    max_field_size: int = DEFAULT_FIELD_TRUNCATE_THRESHOLD,
    protected_paths: frozenset[str] | None = None,
) -> str:
    """
    Format JSON for logging with Unicode support and optional truncation.

    This is the recommended way to format JSON data for log messages.
    It ensures:
    - Unicode characters are preserved (not escaped)
    - Large JSON field values are truncated for readability
    - JSON structure is maintained

    Args:
        data: Data to format as JSON
        indent: Indentation level (default: 2)
        truncate: Whether to truncate large field values (default: True)
        max_field_size: Maximum size for string fields before truncation
        protected_paths: Set of field paths to exclude from truncation

    Returns:
        Formatted JSON string with Unicode characters preserved

    Example:
        >>> logger.info(f"Request: {format_json_for_log(request_dict)}")
        >>> logger.debug(f"Response: {format_json_for_log(response, truncate=False)}")
    """
    # Apply truncation if requested
    if truncate:
        data = truncate_json_fields(
            data, max_field_size=max_field_size, protected_paths=protected_paths
        )

    # Format with Unicode support
    return json.dumps(data, indent=indent, ensure_ascii=False)


def format_json_compact(
    data: Any, truncate: bool = True, protected_paths: frozenset[str] | None = None
) -> str:
    """
    Format JSON compactly (no indentation) for single-line logs.

    Args:
        data: Data to format as JSON
        truncate: Whether to truncate large field values (default: True)
        protected_paths: Set of field paths to exclude from truncation

    Returns:
        Compact JSON string with Unicode characters preserved

    Example:
        >>> logger.info(f"Quick status: {format_json_compact(status_dict)}")
    """
    # Apply truncation if requested
    if truncate:
        data = truncate_json_fields(data, protected_paths=protected_paths)

    # Format compactly with Unicode support
    return json.dumps(data, ensure_ascii=False, separators=(",", ":"))
