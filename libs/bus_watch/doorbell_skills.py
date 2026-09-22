"""Surface-derived doorbell skill slugs — preflighted against ``config/skills.yaml``.

``render_doorbell`` Use-lines and CDP ``skills=`` staging share one derivation
table so a ``cursor_only`` slug (``liaison``) never masquerades as loadable on
life/CDP without an explicit ``skills=`` inline pass (T2' / a:33989).
"""

from __future__ import annotations

from typing import Final

from claude_bundles.catalog import load_skill_catalog

# Body-only echo marker — exists in the liaison skill § Registers,
# not in doorbell paste text; proves ``stage_cdp_prompt_with_skills`` inlined delivery.
LIAISON_PROTOCOL_ECHO: Final = "decision:conductor-attended-vs-unattended-routing"
LIAISON_PROTOCOL_LOADER: Final = "stage_cdp_prompt_with_skills"

_SURFACE_USE_SKILLS: Final[dict[str, tuple[str, ...]]] = {
    "ide": ("liaison", "reasoning-posture"),
    "cursor-sdk": ("liaison", "reasoning-posture"),
    "cdp": ("liaison", "reasoning-posture"),
    "cse": ("liaison", "reasoning-posture"),
    "life": ("liaison", "reasoning-posture"),
}

# Surfaces that seal ``cursor_only`` bodies (prepend or address), not Customize Use-lines.
DISPATCH_SKILLS_SURFACES: Final[frozenset[str]] = frozenset({"cdp", "cse", "life"})
_DISPATCH_SKILLS_SURFACES: Final[frozenset[str]] = DISPATCH_SKILLS_SURFACES

# Only these surfaces may emit ``Use the liaison skill`` (cursor_only self-fetch).
_CURSOR_USE_SURFACES: Final[frozenset[str]] = frozenset({"ide", "cursor-sdk"})


def _is_life_seat(seat: str) -> bool:
    """True when ``seat`` resolves to a claude.ai / life MCP profile."""
    from agent_seat.profiles import get_profile
    from agent_seat.registry import resolve_capability_cell_from_bus_address

    cell = resolve_capability_cell_from_bus_address(seat)
    if cell is None:
        return False
    try:
        prof = get_profile(*cell)
    except KeyError:
        return False
    return prof.mcp_surface == "life"


def _validate_slugs(slugs: tuple[str, ...]) -> tuple[str, ...]:
    catalog = load_skill_catalog(validate_sot=False)
    for slug in slugs:
        catalog.get(slug)
    return slugs


def _preflight(slugs: tuple[str, ...], surface: str) -> tuple[str, ...]:
    """Drop ``cursor_only`` slugs from Use-lines when the surface cannot self-fetch."""
    catalog = load_skill_catalog(validate_sot=False)
    surface_key = str(surface or "ide").strip().lower()
    emit_cursor_only = surface_key in _CURSOR_USE_SURFACES
    kept: list[str] = []
    for slug in slugs:
        entry = catalog.get(slug)
        if entry.surface_class == "cursor_only" and not emit_cursor_only:
            continue
        kept.append(slug)
    return tuple(kept)


def doorbell_skills(surface: str) -> tuple[str, ...]:
    """Use-line slugs for ``render_doorbell``, successor wake, and induction."""
    key = str(surface or "ide").strip().lower()
    slugs = _SURFACE_USE_SKILLS.get(key, _SURFACE_USE_SKILLS["ide"])
    return _preflight(_validate_slugs(slugs), key)


def seat_dispatch_surface(seat: str) -> str | None:
    """Map a dispatch seat id to a surface that seals bodies, not Use-lines, else None."""
    key = str(seat or "cursor-sdk").strip().lower()
    if key.startswith("cdp"):
        return "cdp"
    if key.startswith("cse"):
        return "cse"
    if key in _DISPATCH_SKILLS_SURFACES:
        return key
    if _is_life_seat(seat):
        return "life"
    return None


def seat_doorbell_surface(seat: str) -> str:
    """Map a dispatch seat id to the ``doorbell_skills`` surface key."""
    key = str(seat or "cursor-sdk").strip().lower()
    if key.startswith("cdp"):
        return "cdp"
    if key.startswith("cse"):
        return "cse"
    if key in ("cursor-sdk", "cursor"):
        return "cursor-sdk"
    mapped = seat_dispatch_surface(seat)
    if mapped is not None:
        return mapped
    return key if key in _SURFACE_USE_SKILLS else "ide"


def is_dispatch_skills_surface(seat: str) -> bool:
    """True when the seat's successor wake must inline SOT bodies, not Use-lines."""
    return seat_dispatch_surface(seat) is not None


def dispatch_skills_for_surface(surface: str) -> list[str]:
    """``skills=`` list for ``team_dispatch`` generate when staging must inline."""
    key = str(surface or "").strip().lower()
    if key not in _DISPATCH_SKILLS_SURFACES:
        return []
    slugs = _SURFACE_USE_SKILLS.get(key, ())
    return list(_validate_slugs(slugs))


def liaison_protocol_sot_uri() -> str:
    """Resolvable SOT URI for the house liaison protocol (life doorbell address path)."""
    return load_skill_catalog(validate_sot=False).source_uri_for("liaison")


def navigator_skills_from_policy(
    policy: dict[str, object], *, seat: str = "cursor-sdk"
) -> list[str]:
    """Policy ``navigator_skills`` override ≻ surface-derived delivery list."""
    raw = policy.get("navigator_skills")
    if isinstance(raw, (list, tuple)):
        return [str(s) for s in raw if str(s).strip()]
    surface = seat_dispatch_surface(seat) or seat_doorbell_surface(seat)
    return dispatch_skills_for_surface(surface)


def navigator_doorbell_skills_from_policy(
    policy: dict[str, object], *, seat: str = "cursor-sdk"
) -> tuple[str, ...]:
    """Policy ``navigator_skills`` override ≻ surface-derived Use-line slugs."""
    raw = policy.get("navigator_skills")
    if isinstance(raw, (list, tuple)):
        return tuple(str(s) for s in raw if str(s).strip())
    return doorbell_skills(seat_doorbell_surface(seat))


def primary_liaison_slug(surface: str = "ide") -> str:
    """First skill slug for prose that names the house protocol (IDE hop, induction)."""
    key = str(surface or "ide").strip().lower()
    return _SURFACE_USE_SKILLS.get(key, _SURFACE_USE_SKILLS["ide"])[0]


__all__ = [
    "DISPATCH_SKILLS_SURFACES",
    "LIAISON_PROTOCOL_ECHO",
    "LIAISON_PROTOCOL_LOADER",
    "dispatch_skills_for_surface",
    "doorbell_skills",
    "is_dispatch_skills_surface",
    "liaison_protocol_sot_uri",
    "navigator_doorbell_skills_from_policy",
    "navigator_skills_from_policy",
    "primary_liaison_slug",
    "seat_dispatch_surface",
    "seat_doorbell_surface",
]
