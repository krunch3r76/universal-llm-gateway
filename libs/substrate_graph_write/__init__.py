"""Shared cortex assert author for substrate graph writes.

MCP ``substrate_graph_write`` and GIW ``substrate_feedback`` both import
:func:`write_claim` so cortex-api ``assert`` has one POST author. Domain
isolation: neither mcp-server nor git_integration_worker imports the other.
"""

from __future__ import annotations

from substrate_graph_write.write import write_claim

# Harvest nominates these manage slugs when this lib lands (package-grain).
CONSUMERS: tuple[str, ...] = ('git_integration_worker', 'mcp')

__all__ = ["write_claim"]
