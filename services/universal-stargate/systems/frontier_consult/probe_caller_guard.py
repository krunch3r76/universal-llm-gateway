"""MCP probe caller identity — cost-overhaul Rec 4 / Wave 0.3.

Probes must not enter the reviewer hydration path. Prefer chat ``-mcp`` or
``role=artisan|skeptic``. See ``agent_skill:mcp-tool-loop-trace-matrix``.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any

from .events import FrontierEndpointRejected

EventPublisher = Callable[[Any], None]

_PROBE_CALLER_AGENT_RE = re.compile(
    r"^(mcp-l\d+-probe|mcp-trace-matrix)(-|$)",
    re.IGNORECASE,
)


def is_mcp_probe_caller(caller_agent: str | None) -> bool:
    """True when ``caller_agent`` is a probe ladder identity."""
    if not caller_agent:
        return False
    return _PROBE_CALLER_AGENT_RE.match(caller_agent.strip()) is not None


def reject_probe_on_reviewer(
    role: str,
    *,
    request_id: str,
    caller_agent: str | None,
    event_publisher: EventPublisher | None = None,
) -> None:
    """Raise 422 ``probe_reviewer_forbidden`` when a probe hits role=reviewer."""
    if role != "reviewer" or not is_mcp_probe_caller(caller_agent):
        return
    # Lazy import avoids cycle: admission → this module → FrontierEndpointError
    from .admission import FrontierEndpointError

    reason = (
        f"caller_agent={caller_agent!r} is an MCP probe identity; "
        f"role=reviewer is forbidden for probes (cost-overhaul Rec 4). "
        f"Use /v1/chat/completions with a -mcp model, or team_dispatch "
        f"role=artisan|skeptic / frontier dispatch — never reviewer."
    )
    if event_publisher is not None:
        event_publisher(
            FrontierEndpointRejected(
                request_id=request_id,
                agent=role,
                field="caller_agent",
                reason=reason,
            )
        )
    raise FrontierEndpointError(
        request_id=request_id,
        field="caller_agent",
        reason=reason,
        status_code=422,
        code="probe_reviewer_forbidden",
    )
