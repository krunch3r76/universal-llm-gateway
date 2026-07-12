"""SOT resolution and inline transform for claude.ai skill bundles."""

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

# Shared-sync: cursor-SOT, rendered to .claude/skills by gen_claude_bundles.
_SHARED_SYNC_BRIDGE: list[str] = [
    "fs",
    "cortex-orientation",
    "dispatch-shape",
    "agent-bus-discipline",
    "session-close-kernel",
    "session-close-audit",
    "handoff-pickup",
    "required-skills-pickup",
    "completion-provenance-discipline",
    "cortex-provenance-discipline",
    "consult-routing",
    "model-tier-awareness-web",
    "lead-seat-boot",
]

_SHARED_SYNC_POSTURE: list[str] = [
    "operator-posture",
    "frontier-reasoning-discipline",
    "no-silent-inference",
    "consensus-steelman-posture",
    "advisor-timing",
]

_SHARED_SYNC_LIFE_MATTER: list[str] = [
    "evidence-review-discipline",
    "entity-creation-discipline",
    "entity-lifecycle-discipline",
]

_SHARED_SYNC_META: list[str] = [
    "claude-ai-skill-uninstall",
]

SHARED_SYNC_SLUGS: list[str] = (
    _SHARED_SYNC_BRIDGE
    + _SHARED_SYNC_POSTURE
    + _SHARED_SYNC_LIFE_MATTER
    + _SHARED_SYNC_META
)

# Life-local: .claude/skills SOT — hand-edit, upload, never cursor-indexed.
LIFE_LOCAL_SLUGS: list[str] = [
    "matter-playbook-lifecycle",
    "financial-reasoning",
    "email-bridge-mailbox",
    "email-tool-dispatch",
    "document-review-timeline-linkage-audit",
    "named-entity-verification-gate",
    "engagement-stance",
    "srm",
    "prose-discipline",
]

UI_TARGET_SLUGS: list[str] = list(
    dict.fromkeys([*SHARED_SYNC_SLUGS, *LIFE_LOCAL_SLUGS])
)

# IDE-authored SOT under .cursor/skills/ (authoritative body, not a defer stub).
WORKSPACE_SOT_SLUGS: frozenset[str] = frozenset(
    {
        "add-mcp-tool",
        "produce-uml",
    }
)

# Indexed for cursor hardlink but excluded from Claude.ai standing catalog.
CURSOR_ONLY_SLUGS: list[str] = [
    "add-mcp-tool",
    "agent-bus-multitask",
    "build-pipeline",
    "corpus-cross-reference-discipline",
    "corpus-map-authoring",
    "cursor-rule-authoring",
    "cursor-sdk-instruction-standard",
    "debug-with-events",
    "descriptor-authoring-discipline",
    "document-ingestion",
    "document-lifecycle-tracking",
    "docx-ingestion",
    "image-video-generation",
    "implement-work-item",
    "lead-agent-git-integration",
    "mcp-surface-change",
    "mcp-tool-loop-trace-matrix",
    "pipeline-substrate-capabilities",
    "produce-uml",
    "provenance-granularity",
    "rag-canonical-reference-reminder",
    "refine-pipeline",
    "research-article-ingest",
    "research-article-search",
    "review-task-guidance",
    "service-lifecycle",
    "subgraph-render",
    "thirdparty-api-mirror",
    "ulg-architecture",
    "tax",
    "w2-ingestion",
    "legal-opinion-corpus-ingestion",
    "crypto-trading-research",
    "case-evidence-retrieval",
    "lawyer-stance",
    "psych-framework-counsel",
]

CURSOR_INDEXED_SLUGS: list[str] = list(
    dict.fromkeys([*SHARED_SYNC_SLUGS, *CURSOR_ONLY_SLUGS])
)

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
    """Registry surface class for *slug* (unlisted cursor skills → cursor_only)."""
    if slug in LIFE_LOCAL_SLUGS:
        return "life_local"
    if slug in SHARED_SYNC_SLUGS:
        return "shared_sync"
    return "cursor_only"


@lru_cache(maxsize=1)
def cortex_sot_only_slugs() -> frozenset[str]:
    """Slugs whose authoritative SOT is cortex-mount only (``sot: cortex`` frontmatter)."""
    return frozenset()


def resolve_sot(slug: str, repo_root: Path) -> tuple[Path, str]:
    """Return the first existing SOT path and a short root label for reporting."""
    if slug in LIFE_LOCAL_SLUGS:
        sot_path = repo_root / ".claude" / "skills" / slug / "SKILL.md"
        if sot_path.is_file():
            return sot_path, ".claude/skills"
        raise FileNotFoundError(
            f"no life-local SOT for {slug!r} — searched: {sot_path}"
        )
    sot_path = repo_root / ".cursor" / "skills" / slug / "SKILL.md"
    if sot_path.is_file():
        return sot_path, ".cursor/skills"
    raise FileNotFoundError(f"no SOT for {slug!r} — searched: {sot_path}")


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
    name = str(fm.get("name") or slug)
    description = resolve_bundle_description(
        slug,
        frontmatter=fm,
        body=cleaned,
        entity_description=entity_description,
    )
    header = f"---\nname: {name}\ndescription: {_yaml_scalar(description)}\n---\n\n"
    return header + cleaned if cleaned else header
