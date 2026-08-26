"""Mount-aware caller_agent inference for conductor dispatches."""

from __future__ import annotations

from request_profile import current_request_metadata

from ._agent_bus_author import default_from_for_surface


def infer_caller_agent_for_conductor(
    caller_agent: str | None,
    *,
    packet_kind: str | None,
) -> str | None:
    """Stamp mount-default caller when conductor spawn omits explicit provenance."""
    if isinstance(caller_agent, str) and caller_agent.strip():
        return caller_agent.strip()
    if (packet_kind or "").lower() != "conductor":
        return None
    meta = current_request_metadata()
    surface = meta.get("surface")
    if isinstance(surface, str):
        return default_from_for_surface(surface)
    return None
