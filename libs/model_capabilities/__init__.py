"""Per-model frontier dispatch capability cards (MCP loop, connector, mounts)."""

from .model_capabilities import (
    CARD_VERSION,
    MODEL_CAPABILITY_CARDS,
    CapabilityCardError,
    ModelCapabilityCard,
    capability_card,
    inline_only,
    mcp_capable,
    mcp_client_tool_loop,
    mcp_remote_connector,
    server_side_tools,
    skills_mount_backend,
)

# Harvest nominates these manage slugs when this lib lands (package-grain).
CONSUMERS: tuple[str, ...] = ('stargate',)

__all__ = [
    "CARD_VERSION",
    "MODEL_CAPABILITY_CARDS",
    "CapabilityCardError",
    "ModelCapabilityCard",
    "capability_card",
    "inline_only",
    "mcp_capable",
    "mcp_client_tool_loop",
    "mcp_remote_connector",
    "server_side_tools",
    "skills_mount_backend",
]
