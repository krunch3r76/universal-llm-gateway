"""Mount-aware seat resolution for cortex_brief / boot_inspect."""

from __future__ import annotations

from typing import Any

from agent_seat.registry import (
    normalize_agent_slug,
    normalize_bus_address,
    resolve_capability_cell_from_bus_address,
)
from request_profile import current_request_metadata

from .._agent_bus_author import default_from_for_surface


def parse_seat_slug(slug: str) -> tuple[str | None, str | None]:
    """Parse a seat slug or bus address into (family, platform)."""
    if not slug:
        return None, None
    resolved = resolve_capability_cell_from_bus_address(slug)
    if resolved is not None:
        return resolved
    parts = slug.split("-", 1)
    if len(parts) == 2:
        return parts[0], parts[1]
    return None, None


_SEAT_CLASS_TO_SURFACE = {
    "claude": "life",
    "cursor": "code",
}


def _active_mount_surface(mount_surface: str | None = None) -> str | None:
    """Resolve life|code from request metadata, then registration-time mount."""
    meta = current_request_metadata()
    surface = meta.get("surface")
    if isinstance(surface, str) and surface in ("life", "code"):
        return surface
    seat_class = meta.get("seat_class")
    if isinstance(seat_class, str):
        mapped = _SEAT_CLASS_TO_SURFACE.get(seat_class)
        if mapped:
            return mapped
    if mount_surface in ("life", "code"):
        return mount_surface
    return None


def _surface_default_family_platform(
    mount_surface: str | None = None,
) -> tuple[str, str] | None:
    """Map the active MCP mount surface to (family, platform), if known."""
    surface = _active_mount_surface(mount_surface)
    if not surface:
        return None
    addr = default_from_for_surface(surface)
    if not addr:
        return None
    return resolve_capability_cell_from_bus_address(normalize_bus_address(addr))


def _seat_required_error() -> dict[str, Any]:
    return {
        "error": (
            "seat is required when the MCP mount surface cannot be inferred — "
            'pass seat="web-anthropic" or seat="cursor" '
            "(or a capability cell like seat=\"grok-api-multi\"), "
            "or call from /mcp/life or /mcp/code."
        ),
        "reason": "seat_required",
        "missing_fields": ["seat"],
    }


def resolve_boot_family_platform(
    *,
    seat: str | None = None,
    mount_surface: str | None = None,
) -> tuple[str, str] | dict[str, Any]:
    """Map boot call axes to canonical (family, platform).

    Priority:
      1. Explicit ``seat=`` (bus address or legacy capability cell)
      2. Blank → mount-aware default (life→web / code→cursor)
      3. Else ``seat_required`` error (no silent cursor default)

    ``family`` / ``platform`` are not wire params — they are inferred from
    ``seat`` or the mount. ``mount_surface`` is the FastMCP instance's
    registration-time surface (life|code) when request metadata omits it.
    """
    if seat:
        bus_addr = normalize_bus_address(seat)
        resolved = resolve_capability_cell_from_bus_address(bus_addr)
        if resolved is not None:
            return resolved
        slug = normalize_agent_slug(seat)
        parsed = parse_seat_slug(slug)
        if parsed[0] is not None and parsed[1] is not None:
            return parsed[0], parsed[1]
        return {
            "error": (
                f"seat={seat!r} did not resolve to a known bus address or "
                "capability cell (e.g. web-anthropic, cursor, grok-api-multi)."
            ),
            "reason": "seat_unresolved",
            "missing_fields": ["seat"],
        }

    surface_fp = _surface_default_family_platform(mount_surface)
    if surface_fp is not None:
        return surface_fp
    return _seat_required_error()
