"""Neutral host predicates shared outside cursor_bridge."""

from host_predicates.cursor_hosts import is_cursor_xvfb_host

# Harvest mints one sync_restart row per consumer when this shared lib lands.
CONSUMERS: tuple[str, ...] = (
    "agent_bus",
    "cdp_ask",
    "git_integration_worker",
    "mcp",
    "stargate",
)

__all__ = ["CONSUMERS", "is_cursor_xvfb_host"]
