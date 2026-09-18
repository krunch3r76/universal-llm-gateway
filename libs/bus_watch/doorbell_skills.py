"""Surface-derived doorbell skill slugs — preflighted against ``config/skills.yaml``.

``render_doorbell`` Use-lines and CDP ``skills=`` staging share one derivation
table so a ``cursor_only`` slug (``liaison``) never masquerades as loadable on
life/CDP without an explicit ``skills=`` inline pass (T2' / a:33989).
"""

from __future__ import annotations

from typing import Final

from claude_bundles.catalog import load_skill_catalog

# Body-only echo marker — exists in ``.cursor/skills/liaison/SKILL.md`` § Registers,
# not in doorbell paste text; proves ``stage_cdp_prompt_with_skills`` inlined delivery.
LIAISON_PROTOCOL_ECHO: Final = "decision:conductor-attended-vs-unattended-routing"
LIAISON_PROTOCOL_LOADER: Final = "stage_cdp_prompt_with_skills"

_SURFACE_USE_SKILLS: Final[dict[str, tuple[str, ...]]] = {
    "ide": ("liaison", "reasoning-posture"),
    "cursor-sdk": ("liaison", "reasoning-posture"),
    "cdp": ("liaison", "reasoning-posture"),
    "cse": ("liaison", "reasoning-posture"),
}

# CDP/CSE successors seal ``cursor_only`` bodies via ``prepend_cdp_dispatch_skills``;
# the composer must not emit Customize self-fetch Use-lines on these surfaces.
DISPATCH_SKILLS_SURFACES: Final[frozenset[str]] = frozenset({"cdp", "cse"})
_DISPATCH_SKILLS_SURFACES: Final[frozenset[str]] = DISPATCH_SKILLS_SURFACES


def _preflight(slugs: tuple[str, ...]) -> tuple[str, ...]:
    catalog = load_skill_catalog(validate_sot=False)
    for slug in slugs:
        catalog.get(slug)
    return slugs


def doorbell_skills(surface: str) -> tuple[str, ...]:
    """Use-line slugs for ``render_doorbell``, successor wake, and induction."""
    key = str(surface or "ide").strip().lower()
    slugs = _SURFACE_USE_SKILLS.get(key, _SURFACE_USE_SKILLS["ide"])
    return _preflight(slugs)


def seat_dispatch_surface(seat: str) -> str | None:
    """Map a dispatch seat id to ``cdp`` / ``cse`` when skills must inline, else None."""
    key = str(seat or "cursor-sdk").strip().lower()
    if key.startswith("cdp"):
        return "cdp"
    if key.startswith("cse"):
        return "cse"
    if key in _DISPATCH_SKILLS_SURFACES:
        return key
    return None


def is_dispatch_skills_surface(seat: str) -> bool:
    """True when the seat's successor wake must inline SOT bodies, not Use-lines."""
    return seat_dispatch_surface(seat) is not None


def dispatch_skills_for_surface(surface: str) -> list[str]:
    """``skills=`` list for ``team_dispatch`` generate when staging must inline."""
    key = str(surface or "").strip().lower()
    if key not in _DISPATCH_SKILLS_SURFACES:
        return []
    return list(doorbell_skills(key))


def navigator_skills_from_policy(policy: dict[str, object]) -> list[str]:
    """Policy ``navigator_skills`` override ≻ surface-derived CDP delivery list."""
    raw = policy.get("navigator_skills")
    if isinstance(raw, (list, tuple)):
        return [str(s) for s in raw if str(s).strip()]
    return dispatch_skills_for_surface("cdp")


def navigator_doorbell_skills_from_policy(policy: dict[str, object]) -> tuple[str, ...]:
    """Policy ``navigator_skills`` override ≻ surface-derived Use-line slugs."""
    raw = policy.get("navigator_skills")
    if isinstance(raw, (list, tuple)):
        return tuple(str(s) for s in raw if str(s).strip())
    return doorbell_skills("cdp")


def primary_liaison_slug(surface: str = "ide") -> str:
    """First skill slug for prose that names the house protocol (IDE hop, induction)."""
    return doorbell_skills(surface)[0]


__all__ = [
    "DISPATCH_SKILLS_SURFACES",
    "LIAISON_PROTOCOL_ECHO",
    "LIAISON_PROTOCOL_LOADER",
    "dispatch_skills_for_surface",
    "doorbell_skills",
    "is_dispatch_skills_surface",
    "navigator_doorbell_skills_from_policy",
    "navigator_skills_from_policy",
    "primary_liaison_slug",
    "seat_dispatch_surface",
]
