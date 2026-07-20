"""String helpers for normalizing model metadata fields in API responses.

Provides small formatting utilities shared by chat-template inference and
comprehensive model info extraction across the metadata adapter package.
"""


def safe_lower(value: str | None) -> str:
    """Safely convert a value to lowercase string, handling None."""
    return (value or "").lower()
