"""Whether the dispatch requester can independently verify closeout claims.

Answers the **cause**, not a lane label: can the requester re-observe the
deliverable with their own toolset?

v1 is allowlist-backed with **deny-by-default** polarity — an unknown requester
is treated as not auditable (status clamp on missing §2 reporting fields).

**Residual risk (Fable 6530, 0.85):** this allowlist could ossify into a
de-facto lane label if maintainers add entries by correlate ("life seat") rather
than by verification capability. Deny-by-default mitigates fail-open on novel
blind lanes; it does not eliminate label drift — document edits here, not a
hardcoded ``is_life_to_code`` check elsewhere.
"""

from __future__ import annotations

# Requesters who can read cortex/workspaces/agent-bus and re-verify claims.
# ¬ keyed on ``life`` or ``lane:*`` tags — only on re-observability.
_AUDITABLE_REQUESTERS: frozenset[str] = frozenset(
    {
        "cursor",
        "web",
    }
)


def caller_auditable(*, from_agent: str | None) -> bool:
    """Return True when *from_agent* can independently verify relayed claims."""
    normalized = (from_agent or "").strip().casefold()
    if not normalized:
        return False
    return normalized in {agent.casefold() for agent in _AUDITABLE_REQUESTERS}


__all__ = ["caller_auditable"]
