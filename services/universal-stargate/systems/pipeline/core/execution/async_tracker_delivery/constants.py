"""HTTP timeouts and body-size limits for agent-bus delivery.

Constants live module-level (rather than imported from ``agent_bus_store``)
so this delivery package stays self-contained for testing without pulling
in the agent-bus storage layer dependency tree.
"""

from __future__ import annotations

# Per-request HTTP timeout for agent-bus calls (POST /turns, PATCH close,
# GET thread). Wraps ``httpx.AsyncClient`` via ``make_async_client``.
_HTTP_TIMEOUT_S = 15.0

# Bus turn body hard limit. Mirrors
# ``libs/agent_bus_store/turns_models.MAX_TURN_BODY_CHARS`` — kept as a
# module-level constant rather than imported so this package stays
# self-contained for testing without the agent_bus_store dependency tree.
_BUS_MAX_BODY_CHARS = 8_000

# Threshold below which we post inline without ``allow_long_body=true``. Above
# this, opt into long-body to suppress the briefing-rule 413 envelope; still
# under the hard limit either way.
_BUS_BRIEFING_RULE_CHARS = 1_500
