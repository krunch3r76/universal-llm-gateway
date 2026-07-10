"""Deterministic skill→skill reference extraction from repo .cursor/skills SOT bodies."""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path

_BARE_SLUG_RE = re.compile(r"^[a-z0-9-]+$")
_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---", re.DOTALL)
_RELATED_SKILLS_SECTION_RE = re.compile(
    r"^## Related skills\s*\n((?:[-*]\s+[a-z0-9-]+\s*\n)+)",
    re.MULTILINE,
)
_FENCE_RE = re.compile(r"```.*?```", re.DOTALL)

_REF_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(
        r"workspaces://universal-llm-gateway/\.cursor/skills/([a-z0-9-]+)/SKILL\.md",
        re.I,
    ),
    re.compile(r"(?<![\w/])\.cursor/skills/([a-z0-9-]+)/SKILL\.md", re.I),
    re.compile(r"cortex://agent-skills/([a-z0-9-]+)\.md", re.I),
    re.compile(r"(?<![\w/])agent-skills/([a-z0-9-]+)\.md", re.I),
    re.compile(r"agent_skill:([a-z0-9-]+)", re.I),
    re.compile(r"\]\(([a-z0-9-]+)\.md(?:#[^)]*)?\)"),
)

SKIP_SOURCE_NAMES = frozenset({"README"})
SKIP_TARGET_SLUGS = frozenset(
    {
        "grokbuild",
        "grokbuild-v1",
        "grokbuild-v2",
        "presence-discipline",
    }
)
INVARIANT_TARGETS = frozenset({"architecture-invariants", "ulg-architecture"})

_REPO_DEFAULT = Path(__file__).resolve().parent.parent.parent


@dataclass(frozen=True)
class MinedEdge:
    source: str
    target: str
    rel_type: str = "references"
    strength: float = 0.6
    role: str | None = "sot_pointer"


def default_repo_root() -> Path:
    return _REPO_DEFAULT


def default_workspaces_root() -> Path:
    return Path(os.environ.get("WORKSPACES_ROOT", "/mnt/torus/projects")).expanduser()


def _strip_fences(text: str) -> str:
    return _FENCE_RE.sub("", text)


def _parse_frontmatter_related(text: str) -> list[str]:
    match = _FRONTMATTER_RE.match(text)
    if not match:
        return []
    for line in match.group(1).splitlines():
        if not line.startswith("related_skills:"):
            continue
        raw = line.split(":", 1)[1].strip()
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return []
        if isinstance(parsed, list):
            return [str(v).split("#", 1)[0].strip() for v in parsed]
    return []


def _parse_related_section(text: str) -> list[str]:
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
        if _BARE_SLUG_RE.match(slug) and slug not in slugs:
            slugs.append(slug)
    return slugs


def _target_exists(slug: str, *, skills_root: Path, ws_root: Path) -> bool:
    if (skills_root / slug / "SKILL.md").exists():
        return True
    return (
        ws_root / "universal-llm-gateway" / ".cursor" / "skills" / slug / "SKILL.md"
    ).exists()


def _infer_role(target: str) -> str:
    if target in INVARIANT_TARGETS:
        return "invariant"
    return "sot_pointer"


def iter_sot_paths(repo_root: Path) -> list[Path]:
    skills_dir = repo_root / ".cursor" / "skills"
    paths: list[Path] = []
    for path in sorted(skills_dir.glob("*/SKILL.md")):
        if path.parent.name in SKIP_SOURCE_NAMES:
            continue
        paths.append(path)
    return paths


def mine_sot_file(
    path: Path,
    *,
    skills_root: Path,
    ws_root: Path,
    valid_targets: set[str] | None = None,
) -> set[str]:
    source = path.parent.name
    text = path.read_text(encoding="utf-8", errors="replace")
    targets: set[str] = set()
    for slug in (
        *_parse_frontmatter_related(text),
        *_parse_related_section(text),
    ):
        if slug != source and slug not in SKIP_TARGET_SLUGS:
            if valid_targets is None or slug in valid_targets:
                if _target_exists(slug, skills_root=skills_root, ws_root=ws_root):
                    targets.add(slug)
    body = _strip_fences(text)
    for pattern in _REF_PATTERNS:
        for match in pattern.finditer(body):
            slug = match.group(1).lower()
            if slug == source or slug in SKIP_TARGET_SLUGS:
                continue
            if valid_targets is not None and slug not in valid_targets:
                continue
            if _target_exists(slug, skills_root=skills_root, ws_root=ws_root):
                targets.add(slug)
    return targets


def mine_all_sot_edges(
    *,
    repo_root: Path | None = None,
    ws_root: Path | None = None,
    valid_targets: set[str] | None = None,
) -> dict[str, set[str]]:
    root = repo_root or default_repo_root()
    skills_dir = root / ".cursor" / "skills"
    workspace = ws_root or default_workspaces_root()
    mined: dict[str, set[str]] = {}
    for path in iter_sot_paths(root):
        targets = mine_sot_file(
            path,
            skills_root=skills_dir,
            ws_root=workspace,
            valid_targets=valid_targets,
        )
        if targets:
            mined[path.parent.name] = targets
    return mined


def mined_to_edges(mined: dict[str, set[str]]) -> list[MinedEdge]:
    edges: list[MinedEdge] = []
    for source in sorted(mined):
        for target in sorted(mined[source]):
            role = _infer_role(target)
            strength = 0.6 if role != "invariant" else 0.6
            edges.append(MinedEdge(source, target, "references", strength, role))
    return edges
