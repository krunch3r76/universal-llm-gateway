"""Tool-loop turn budgets by dispatch substrate.

Policy:
- ``cursor/*`` / ``role=cursor-sdk`` — unbounded. The Cursor agent loop is not
  capped by ``max_tool_turns``; it runs until completion or outer timeout.
- API roles / native tool loop — default ``API_DEFAULT_MAX_TOOL_TURNS`` when
  the caller omits ``max_tool_turns``.
"""

from __future__ import annotations

# Default for API role generate / frontier-dispatch handler when omitted.
API_DEFAULT_MAX_TOOL_TURNS = 150
