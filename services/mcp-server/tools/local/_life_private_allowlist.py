"""Private ``tools.local`` modules permitted on the /mcp/life overflow catalog.

Code surface loads the full ``tools.local`` tree; life loads only this subset
so operator verification tools (email) are reachable without exposing finance,
bot_supervisor, or other code-seat private domains.
"""

from __future__ import annotations

LIFE_PRIVATE_TOOL_MODULES: frozenset[str] = frozenset({"email"})
