"""Out-of-band schema channel default (A4)."""

from __future__ import annotations

SCHEMA_CHANNEL_DEFAULT = "cortex_schema(op)"


def schema_channel_doc() -> str:
    """Document the default schema channel when MCP resources are unproven."""
    return (
        "Schema channel defaults to generated "
        f"`{SCHEMA_CHANNEL_DEFAULT}` unless MCP-resource consumption is "
        "confirmed on all live seats (life / web-anthropic / cursor-sdk). "
        "Do not assume `cortex://openapi/operations/{operationId}` resources "
        "everywhere."
    )
