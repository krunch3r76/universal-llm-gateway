"""Rendered boot-surface skill pointer probe (F5 / AC21)."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from implement_admission.skill_catalog_resolver import (
    SkillCatalogResolveError,
    resolve_canonical_source_uri,
)

_AGENT_SKILL_TOKEN = re.compile(
    r"agent_skill:([a-z0-9][-a-z0-9_]*)",
    re.IGNORECASE,
)
_AGENT_SKILLS_PATH = re.compile(
    r"(?:agent-skills|agent_skills)/([a-z0-9][-a-z0-9_]*)\.md",
    re.IGNORECASE,
)
_CURSOR_SKILL_PATH = re.compile(
    r"\.cursor/skills/([a-z0-9][-a-z0-9_]*)/SKILL\.md",
    re.IGNORECASE,
)
_JSON_EMBEDDED_SLUG = re.compile(
    r'"id"\s*:\s*"agent_skill:([a-z0-9][-a-z0-9_]*)"',
    re.IGNORECASE,
)

_DOC_ONLY_BANNERS = frozenset({"web-boot-lead"})


@dataclass(frozen=True, slots=True)
class SurfacePointerViolation:
    pointer: str
    slug: str
    reason: str
    detail: str | None = None


@dataclass(frozen=True, slots=True)
class SurfaceProbeReport:
    ok: bool
    pointers_checked: int
    violations: tuple[SurfacePointerViolation, ...]


def _extract_slugs(text: str) -> set[str]:
    slugs: set[str] = set()
    for pattern in (
        _AGENT_SKILL_TOKEN,
        _AGENT_SKILLS_PATH,
        _CURSOR_SKILL_PATH,
        _JSON_EMBEDDED_SLUG,
    ):
        for match in pattern.finditer(text):
            slugs.add(match.group(1).lower())
    return slugs


def probe_rendered_surface(
    text: str,
    *,
    platform: str = "web",
) -> SurfaceProbeReport:
    """Resolve every emitted skill pointer against the skill catalog (fail-loud)."""
    violations: list[SurfacePointerViolation] = []
    slugs = _extract_slugs(text)
    for slug in sorted(slugs):
        if slug in _DOC_ONLY_BANNERS:
            violations.append(
                SurfacePointerViolation(
                    pointer=slug,
                    slug=slug,
                    reason="doc_only_no_entity",
                    detail="web-boot-lead demote-default — no backing entity",
                )
            )
            continue
        try:
            uri = resolve_canonical_source_uri(slug)
        except SkillCatalogResolveError as exc:
            violations.append(
                SurfacePointerViolation(
                    pointer=slug,
                    slug=slug,
                    reason="unresolved_slug",
                    detail=str(exc),
                )
            )
            continue
        if platform == "web" and uri.startswith(".cursor/skills/"):
            violations.append(
                SurfacePointerViolation(
                    pointer=slug,
                    slug=slug,
                    reason="seat_inappropriate_uri",
                    detail=f"cursor-only path on web surface: {uri!r}",
                )
            )
    return SurfaceProbeReport(
        ok=not violations,
        pointers_checked=len(slugs),
        violations=tuple(violations),
    )


def probe_boot_manifest(manifest: dict[str, Any], *, platform: str = "web") -> SurfaceProbeReport:
    """Walk a boot manifest dict (card markdown + injected artifacts) for pointers."""
    parts: list[str] = []
    card = manifest.get("briefing_card") or manifest.get("briefing_card_md")
    if isinstance(card, str):
        parts.append(card)
    for key in ("skills_card_markdown", "skills_concise_markdown", "operational_context"):
        val = manifest.get(key)
        if isinstance(val, str):
            parts.append(val)
    for artifact in manifest.get("injected_artifacts") or []:
        if isinstance(artifact, dict):
            parts.append(json.dumps(artifact, sort_keys=True))
    return probe_rendered_surface("\n".join(parts), platform=platform)
