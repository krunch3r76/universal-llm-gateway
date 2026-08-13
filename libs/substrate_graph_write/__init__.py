"""Shared cortex assert author for substrate graph writes.

MCP ``substrate_graph_write`` and GIW ``substrate_feedback`` both import
:func:`write_claim` so cortex-api ``assert`` has one POST author. Domain
isolation: neither mcp-server nor git_integration_worker imports the other.
"""

from __future__ import annotations

from substrate_graph_write.write import write_claim

__all__ = ["write_claim"]
