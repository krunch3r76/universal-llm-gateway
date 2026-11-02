"""Boot briefing identity — capability-cell resolution for cortex_brief and boot_inspect.

Callers pass a seat slug or bus address; this module maps known capability cells
to family/platform/agent fields used by MCP boot fetch graphs. Unknown seats return
structured ``seat_unresolved`` errors rather than silent defaults.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .profiles import family_anchor, get_profile
from .registry import (
    normalize_agent_slug,
    normalize_bus_address,
    resolve_capability_cell_from_bus_address,
)


@dataclass(frozen=True, slots=True)
class BootIdentity:
    """Resolved identity for cortex_brief / boot_inspect (capability cell)."""

    family: str
    platform: str
    agent: str
    session_prefix: str
    inbox_recipient: str
    self_entity_id: str

    @property
    def capability_slug(self) -> str:
        return f"{self.family}-{self.platform}"


def legacy_boot_identity(family: str, platform: str) -> BootIdentity:
    """Reproduce boot identity for a known (family, platform) capability cell."""
    capability_slug = f"{family}-{platform}"
    return BootIdentity(
        family=family,
        platform=platform,
        agent=capability_slug,
        session_prefix=normalize_bus_address(capability_slug),
        inbox_recipient=capability_slug,
        self_entity_id=family_anchor(family),
    )


def resolve_legacy_boot_identity(seat: str) -> BootIdentity | None:
    """Resolve capability-cell identity from a seat slug or bus address."""
    return _legacy_from_capability_cell(seat)


def _legacy_from_capability_cell(seat: str) -> BootIdentity | None:
    bus_addr = normalize_bus_address(seat)
    resolved = resolve_capability_cell_from_bus_address(bus_addr)
    if resolved is None:
        slug = normalize_agent_slug(seat)
        parts = slug.split("-", 1)
        if len(parts) == 2:
            try:
                get_profile(parts[0], parts[1])
            except KeyError:
                return None
            return legacy_boot_identity(parts[0], parts[1])
        return None
    family, platform = resolved
    try:
        get_profile(family, platform)
    except KeyError:
        return None
    return legacy_boot_identity(family, platform)


def seat_unresolved_error(seat: str) -> dict[str, Any]:
    """Structured error when ``seat`` does not resolve to a known bus address or capability cell."""
    return {
        "error": (
            f"seat={seat!r} did not resolve to a known bus address or capability cell."
        ),
        "reason": "seat_unresolved",
        "missing_fields": ["seat"],
    }


def resolve_boot_identity_for_seat(seat: str) -> BootIdentity | dict[str, Any]:
    """Resolve an explicit ``seat`` string to capability-cell identity, or ``seat_unresolved``."""
    legacy = _legacy_from_capability_cell(seat)
    if legacy is not None:
        return legacy
    return seat_unresolved_error(seat)


def boot_identity_to_wire(identity: BootIdentity) -> dict[str, Any]:
    """Serialize a resolved BootIdentity for cortex_brief / boot_inspect JSON payloads."""
    return {
        "family": identity.family,
        "platform": identity.platform,
        "agent": identity.agent,
        "capability_slug": identity.capability_slug,
        "session_prefix": identity.session_prefix,
        "inbox_recipient": identity.inbox_recipient,
        "self_entity_id": identity.self_entity_id,
        "is_logical": False,
    }
