"""
Format Utilities - Model format helpers.

V2 Architecture:
    All format-specific logic lives in engine schemas.
    This module provides thin wrappers for backward compatibility.

Note:
    Prefer using SchemaRegistry directly for new code.
"""

from .catalog.schemas import SchemaRegistry


def is_named_profile_format(format_type: str | None) -> bool:
    """
    Check if format uses named profiles instead of context-length profiles.

    Named profile formats use descriptive keys like 'default', 'offload'
    instead of numeric context lengths like '8192', '32768'.

    Args:
        format_type: Model format string

    Returns:
        True if format uses named profiles, False otherwise

    Note:
        Delegates to schema's profile_type attribute.
    """
    if not format_type:
        return False

    schema = SchemaRegistry.get_by_format(format_type)
    if not schema:
        return False

    return schema.profile_type == "named"


def get_engine_for_format(format_type: str) -> str | None:
    """
    Get the engine name for a model format.

    Args:
        format_type: Model format string

    Returns:
        Engine name or None if unknown format

    Note:
        Delegates to SchemaRegistry.
    """
    schema = SchemaRegistry.get_by_format(format_type)
    return schema.engine if schema else None
