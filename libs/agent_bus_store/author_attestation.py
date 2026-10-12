"""Credential attestation strings for agent-bus turn rows."""

from __future__ import annotations


def attest(
    auth_mode: object,
    caller_identity: object,
    surface: object,
) -> str | None:
    """Return a stable attestation label when auth context is complete.

    The value names the MCP credential triple (mode, identity, mount surface).
    It is not a bus routing address: ``from`` / ``from_agent`` remain the seat
    address the caller supplied or the surface default, independent of this stamp.
    """
    if not isinstance(auth_mode, str) or not auth_mode.strip():
        return None
    if not isinstance(caller_identity, str) or not caller_identity.strip():
        return None
    if not isinstance(surface, str) or not surface.strip():
        return None
    return f"{auth_mode.strip()}:{caller_identity.strip()}@{surface.strip()}"
