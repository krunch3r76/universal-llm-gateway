"""Shared declared ``related_skills`` parsing for workspace + cortex SOT SKILL.md."""

from __future__ import annotations

import json
import re

_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---", re.DOTALL)
_RELATED_SKILLS_SECTION_RE = re.compile(
    r"^## Related skills\s*\n((?:[-*]\s+[a-z0-9-]+\s*\n)+)",
    re.MULTILINE,
)
BARE_SLUG_RE = re.compile(r"^[a-z0-9-]+$")


def parse_frontmatter(text: str) -> dict[str, object]:
    match = _FRONTMATTER_RE.match(text)
    if not match:
        return {}
    data: dict[str, object] = {}
    for line in match.group(1).splitlines():
        if ":" not in line:
            continue
        key, raw = line.split(":", 1)
        key, raw = key.strip(), raw.strip()
        if not key:
            continue
        if key == "applicable_agents":
            try:
                parsed = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if isinstance(parsed, list):
                data[key] = [str(v) for v in parsed]
            continue
        if key in {"related_skills", "trigger_match_terms"}:
            try:
                parsed = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if isinstance(parsed, list):
                data[key] = [str(v).split("#", 1)[0].strip() for v in parsed]
            continue
        if (raw.startswith('"') and raw.endswith('"')) or (
            raw.startswith("'") and raw.endswith("'")
        ):
            raw = raw[1:-1]
        data[key] = raw
    return data


def parse_related_skills_section(text: str) -> list[str]:
    match = _RELATED_SKILLS_SECTION_RE.search(text)
    if not match:
        return []
    slugs: list[str] = []
    for line in match.group(1).splitlines():
        line = line.strip()
        if not line.startswith(("-", "*")):
            continue
        slug = line.lstrip("-*").strip().split("#", 1)[0].strip()
        if slug.startswith("agent_skill:"):
            slug = slug.removeprefix("agent_skill:")
        if BARE_SLUG_RE.match(slug) and slug not in slugs:
            slugs.append(slug)
    return slugs


def declared_related_skills(text: str, fm: dict[str, object]) -> list[str]:
    from_fm = fm.get("related_skills")
    if isinstance(from_fm, list) and from_fm:
        return [str(v) for v in from_fm]
    return parse_related_skills_section(text)
