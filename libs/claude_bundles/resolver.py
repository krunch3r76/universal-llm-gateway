"""SOT resolution and inline transform for claude.ai skill bundles.

Placement authority: ``config/skills.yaml`` via ``claude_bundles.catalog``.
"""

from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path

from claude_bundles.bundle_description import (
    FrontmatterParseError,
    parse_frontmatter,
    resolve_bundle_description,
)
from claude_bundles.catalog import get_skill_catalog
from claude_bundles.composer_skill_match import normalize_first_h1

_SOT_LINE_RE = re.compile(r"^\*\*SOT")
_SOURCE_LINE_RE = re.compile(r"^\*\*Source:\*\*")
_GENERATED_COMMENT_RE = re.compile(r"GENERATED\s*[—-]\s*DO NOT EDIT")
_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---", re.DOTALL)


def _frontmatter_sot_value(text: str) -> str | None:
    match = _FRONTMATTER_RE.match(text)
    if not match:
        return None
    for line in match.group(1).splitlines():
        if not line.strip().startswith("sot:"):
            continue
        return line.split(":", 1)[1].strip().strip("\"'")
    return None


def _frontmatter_sot_cortex(text: str) -> bool:
    return _frontmatter_sot_value(text) == "cortex"


def _docs_defers_to_cortex(docs_text: str) -> bool:
    if _frontmatter_sot_cortex(docs_text):
        return True
    if "agent-skills/" not in docs_text:
        return False
    defer_markers = (
        "Do not maintain a second long-form copy",
        "Do not duplicate the cortex playbook",
        "the cortex file owns",
    )
    return any(marker in docs_text for marker in defer_markers)


def docs_defers_to_cortex(docs_text: str) -> bool:
    """True when a legacy docs stub defers to cortex SOT, not an authoritative body."""
    return _docs_defers_to_cortex(docs_text)


def is_cortex_sot_frontmatter(text: str) -> bool:
    """True when YAML frontmatter declares ``sot: cortex`` (roadmap 2.3)."""
    return _frontmatter_sot_cortex(text)


def is_claude_sot_frontmatter(text: str) -> bool:
    """True when YAML frontmatter declares ``sot: claude`` (life-local marker)."""
    return _frontmatter_sot_value(text) == "claude"


def surface_class_for_slug(slug: str) -> str:
    """Catalog surface class for *slug*."""
    return get_skill_catalog().surface_class_for(slug)


@lru_cache(maxsize=1)
def cortex_sot_only_slugs() -> frozenset[str]:
    """Slugs whose authoritative SOT is cortex-mount only (``sot: cortex`` frontmatter)."""
    return frozenset()


def resolve_sot(slug: str, repo_root: Path) -> tuple[Path, str]:
    """Return the first existing SOT path and a short root label for reporting."""
    return get_skill_catalog().resolve_sot(slug, repo_root)


def shared_sync_slugs() -> list[str]:
    return get_skill_catalog().shared_sync_slugs()


def life_local_slugs() -> list[str]:
    return get_skill_catalog().life_local_slugs()


def cursor_only_slugs() -> list[str]:
    return get_skill_catalog().cursor_only_slugs()


def cursor_indexed_slugs() -> list[str]:
    return get_skill_catalog().cursor_indexed_slugs()


def claude_ai_target_slugs() -> list[str]:
    return get_skill_catalog().claude_ai_targets()


def _strip_pointer_fences(body: str) -> str:
    lines = body.splitlines()
    out: list[str] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        if "<!--" in line and _GENERATED_COMMENT_RE.search(line):
            i += 1
            continue
        if _SOT_LINE_RE.match(line.strip()) or _SOURCE_LINE_RE.match(line.strip()):
            i += 1
            while i < len(lines) and not lines[i].strip():
                i += 1
            if i < len(lines) and lines[i].strip().startswith("```"):
                i += 1
                while i < len(lines) and not lines[i].strip().startswith("```"):
                    i += 1
                if i < len(lines):
                    i += 1
            continue
        out.append(line)
        i += 1
    cleaned = "\n".join(out).strip()
    return f"{cleaned}\n" if cleaned else ""


def _yaml_scalar(value: str) -> str:
    if not value:
        return '""'
    if re.fullmatch(r"[^\n\"'#,:{}\\[\\]&*!?|>@%]+", value):
        return value
    return json.dumps(value)


def render_bundle(
    slug: str,
    raw: str,
    *,
    entity_description: str | None = None,
) -> str:
    """Inline SOT into a self-contained SKILL.md for claude.ai consumption."""
    try:
        fm, body = parse_frontmatter(raw)
    except FrontmatterParseError:
        match = _FRONTMATTER_RE.match(raw)
        fm = {}
        body = raw[match.end() :].lstrip("\n") if match else raw
    cleaned = _strip_pointer_fences(body)
    cleaned, _ = normalize_first_h1(slug, cleaned)
    name = str(fm.get("name") or slug)
    description = resolve_bundle_description(
        slug,
        frontmatter=fm,
        body=cleaned,
        entity_description=entity_description,
    )
    header = f"---\nname: {name}\ndescription: {_yaml_scalar(description)}\n---\n\n"
    return header + cleaned if cleaned else header
