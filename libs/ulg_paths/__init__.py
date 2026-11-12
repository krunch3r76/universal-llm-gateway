"""Neutral path helpers (no service-layer imports)."""

from ulg_paths.repo_root import ulg_repo_root

CONSUMERS: tuple[str, ...] = ("agent_bus", "mcp", "stargate")

__all__ = ["CONSUMERS", "ulg_repo_root"]
