"""Jobs dispatch op names — the denominator for x-mcp stamping.

These six names are the x-mcp ops, not the OpenAPI operationIds. The HTTP
app stamps each one with ``pipeline=True``.
"""

from __future__ import annotations

JOBS_DISPATCH_OPS: frozenset[str] = frozenset(
    {"catalog", "spec", "create_run", "status", "log", "cancel"}
)
