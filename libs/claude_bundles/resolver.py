"""SOT resolution and inline transform for claude.ai skill bundles."""

from __future__ import annotations

import json
import re
from pathlib import Path

CLAUDE_BUNDLE_SLUGS: list[str] = [
    "research-article-ingest",
    "research-article-search",
    "image-video-generation",
    "subgraph-render",
    "cortex-entity-restructure",
    "thirdparty-api-mirror",
    "multi-model-review",
    "implementation-plan-workflow",
    "handoff-packet-authoring",
    "handoff-prompt-authoring",
    "superheavy-dispatch",
    "document-ingestion",
    "docx-ingestion",
]

CORTEX_SOT_ROOT = Path("/mnt/torus/mcp-data/files/agent-skills")

_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---", re.DOTALL)
_SOT_LINE_RE = re.compile(r"^\*\*SOT")
_SOURCE_LINE_RE = re.compile(r"^\*\*Source:\*\*")
_GENERATED_COMMENT_RE = re.compile(r"GENERATED\s*[—-]\s*DO NOT EDIT")


def resolve_sot(slug: str, repo_root: Path) -> tuple[Path, str]:
    """Return the first existing SOT path and a short root label for reporting."""
    candidates: list[tuple[str, Path]] = [
        (
            "docs/agent-guides/skills",
            repo_root / "docs/agent-guides/skills" / f"{slug}.md",
        ),
        (
            "docs/agent-guides/rules",
            repo_root / "docs/agent-guides/rules" / f"{slug}.md",
        ),
        ("cortex/agent-skills", CORTEX_SOT_ROOT / f"{slug}.md"),
    ]
    for label, path in candidates:
        if path.is_file():
            return path, label
    searched = ", ".join(str(p) for _, p in candidates)
    raise FileNotFoundError(f"no SOT for {slug!r} — searched: {searched}")


def _split_frontmatter(text: str) -> tuple[dict[str, str], str]:
    match = _FRONTMATTER_RE.match(text)
    if not match:
        return {}, text
    fm: dict[str, str] = {}
    for line in match.group(1).splitlines():
        if ":" not in line:
            continue
        key, raw = line.split(":", 1)
        key, raw = key.strip(), raw.strip()
        if key not in {"name", "description"}:
            continue
        if (raw.startswith('"') and raw.endswith('"')) or (
            raw.startswith("'") and raw.endswith("'")
        ):
            raw = raw[1:-1]
        fm[key] = raw
    body = text[match.end() :].lstrip("\n")
    return fm, body


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


def _first_prose_line(body: str) -> str:
    for line in body.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("#"):
            stripped = stripped.lstrip("#").strip()
        if stripped.startswith(("<!--", "```", "|", "- ", "* ")):
            continue
        if _SOT_LINE_RE.match(stripped) or _SOURCE_LINE_RE.match(stripped):
            continue
        return stripped
    return ""


def _yaml_scalar(value: str) -> str:
    if not value:
        return '""'
    if re.fullmatch(r"[^\n\"'#,:{}\\[\\]&*!?|>@%]+", value):
        return value
    return json.dumps(value)


def render_bundle(slug: str, raw: str) -> str:
    """Inline SOT into a self-contained SKILL.md for claude.ai consumption."""
    fm, body = _split_frontmatter(raw)
    cleaned = _strip_pointer_fences(body)
    name = fm.get("name") or slug
    description = fm.get("description") or _first_prose_line(cleaned)
    header = f"---\nname: {name}\ndescription: {_yaml_scalar(description)}\n---\n\n"
    return header + cleaned if cleaned else header
