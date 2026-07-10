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

# claude.ai bundle = all CURSOR_INDEXED skills (matter playbooks retired → case documents).
_CLAUDE_BUNDLE_U: list[str] = [
    "orchestrator-core",
    "markdown-navigation",
    "modularize-discipline",
    "investigation-economy",
    "no-silent-inference",
    "frontier-reasoning-discipline",
    "frontier-model-instructions",
    "prose-discipline",
    "srm",
    "engagement-stance",
]

_CLAUDE_BUNDLE_E: list[str] = [
    "cortex",
    "fs",
    "cortex-orientation",
    "dispatch-shape",
    "dispatch-workflow",
    "consult-routing",
    "agent-bus-discipline",
    "entity-lifecycle-discipline",
    "entity-creation-discipline",
    "cortex-provenance-discipline",
    "auditor-validatable-confidence",
    "completion-provenance-discipline",
    "evidence-review-discipline",
    "enrichment-quality-discipline",
    "boot-execution-discipline",
    "operator-posture",
    "advisor-timing",
    "agent-identity-signoff",
    "consensus-steelman-posture",
    "cheap-recon-before-escalation",
    "task-grouping-discipline",
    "planning-promotion-ladder",
    "required-skills-pickup",
    "skill-suggest-utilization",
    "handoff-pickup",
    "implement-todo",
    "friction-review",
    "descriptor-authoring-discipline",
    "model-tier-awareness-web",
    "session-close",
    "session-close-audit",
    "lead-agent-git-integration",
    "lead-seat-boot",
    "cursor-sdk-instruction-standard",
    "agent-guidance-writing",
    "skill-document-writing",
    "commit-and-git-scope",
    "git-posture",
    "session-close-kernel",
    "session-close-transcript",
    "session-close-handoff",
    "session-close-reflective-journal",
    "cortex-entity-restructure",
    "subgraph-render",
    "handoff-packet-authoring",
    "handoff-prompt-authoring",
    "dispatch-prompt-house-style",
    "implementation-plan-workflow",
    "implement-work-item",
    "multi-model-review",
    "agent-bus-multitask",
    "research-article-ingest",
    "research-article-search",
    "document-ingestion",
    "rag-canonical-reference-reminder",
    "docx-ingestion",
    "image-video-generation",
    "thirdparty-api-mirror",
    "web-generate-substrate",
    "web-transcript-preprocessing",
    "review-task-guidance",
    "email-tool-dispatch",
    "email-bridge-mailbox",
    "document-lifecycle-tracking",
    "architecture-invariants",
    "corpus-map-authoring",
    "corpus-grounded-skill-authoring",
    "todo-lifecycle",
    # Repo / operational SOT — rendered to .claude for parity (cursor hardlink unchanged)
    "build-pipeline",
    "corpus-cross-reference-discipline",
    "cursor-rule-authoring",
    "debug-with-events",
    "mcp-surface-change",
    "mcp-tool-loop-trace-matrix",
    "orchestrator-workflow",
    "pipeline-substrate-capabilities",
    "pre-deploy-gate-discipline",
    "provenance-granularity",
    "refine-pipeline",
    "service-lifecycle",
    "ulg-architecture",
    # IDE-authored SOT under .cursor/skills/ (no separate docs/cortex body)
    "add-mcp-tool",
    "produce-uml",
    # Operator-confirmed 2026-07-09 (a:23206) — was orphaned on claude.ai UI
    "overhaul-program",
]

# Domain / matter skills — flat-upload-safe after E1 remediation (agent-bus 4559 / 4649).
# A distinct class from universal-reasoning (_U) and engineering (_E); the static
# allowlist historically omitted the domain class entirely. Added tactically pending
# the CLAUDE_BUNDLE allowlist->denylist inversion investigation.
_CLAUDE_BUNDLE_D: list[str] = [
    "tax",
    "w2-ingestion",
    "legal-opinion-corpus-ingestion",
    "crypto-trading-research",
    "financial-reasoning",
    "named-entity-verification-gate",
    "case-evidence-retrieval",
    "lawyer-stance",
    "document-review-timeline-linkage-audit",
    "psych-framework-counsel",
]

CLAUDE_BUNDLE_SLUGS: list[str] = _CLAUDE_BUNDLE_U + _CLAUDE_BUNDLE_E + _CLAUDE_BUNDLE_D

# IDE-authored SOT under .cursor/skills/ (authoritative body, not a defer stub).
WORKSPACE_SOT_SLUGS: frozenset[str] = frozenset(
    {
        "add-mcp-tool",
        "produce-uml",
    }
)

# Indexed for cursor hardlink but excluded from .claude render (matter playbook — retiring).
CURSOR_ONLY_SLUGS: list[str] = []

# Back-compat alias (removed next commit window).
CURSOR_SOT_DIRECT_SLUGS: list[str] = CURSOR_ONLY_SLUGS

# Single rule: every indexed cursor skill hardlinks to authoritative SOT.
CURSOR_INDEXED_SLUGS: list[str] = list(
    dict.fromkeys([*CLAUDE_BUNDLE_SLUGS, *CURSOR_ONLY_SLUGS])
)

CORTEX_SOT_ROOT = Path("/mnt/torus/mcp-data/files/agent-skills")

_SOT_LINE_RE = re.compile(r"^\*\*SOT")
_SOURCE_LINE_RE = re.compile(r"^\*\*Source:\*\*")
_GENERATED_COMMENT_RE = re.compile(r"GENERATED\s*[—-]\s*DO NOT EDIT")
_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---", re.DOTALL)


def _frontmatter_sot_cortex(text: str) -> bool:
    match = _FRONTMATTER_RE.match(text)
    if not match:
        return False
    for line in match.group(1).splitlines():
        if not line.strip().startswith("sot:"):
            continue
        value = line.split(":", 1)[1].strip().strip("\"'")
        return value == "cortex"
    return False


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


@lru_cache(maxsize=1)
def cortex_sot_only_slugs() -> frozenset[str]:
    """Slugs whose authoritative SOT is cortex-mount only (``sot: cortex`` frontmatter)."""
    if _cortex_mount_missing():
        return frozenset()
    slugs: set[str] = set()
    for path in CORTEX_SOT_ROOT.glob("*.md"):
        if path.stem == "README":
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        if is_cortex_sot_frontmatter(text):
            slugs.add(path.stem)
    return frozenset(slugs)


def _cortex_mount_missing() -> bool:
    return not CORTEX_SOT_ROOT.is_dir()


def resolve_sot(slug: str, repo_root: Path) -> tuple[Path, str]:
    """Return the first existing SOT path and a short root label for reporting."""
    docs_skills = repo_root / ".cursor" / "skills" / slug / "SKILL.md"
    cortex_sot = CORTEX_SOT_ROOT / f"{slug}.md"
    docs_defer_cortex = False
    if _cortex_mount_missing() and (slug in CURSOR_INDEXED_SLUGS):
        pass  # .cursor is primary; mount optional for indexed slugs
    if docs_skills.is_file() and cortex_sot.is_file() and docs_defer_cortex:
        return cortex_sot, "cortex/agent-skills"
    if docs_defer_cortex and not cortex_sot.is_file():
        raise FileNotFoundError(
            f"no SOT for {slug!r} — docs stub defers to missing {cortex_sot}"
        )
    candidates: list[tuple[str, Path]] = [
        (
            ".cursor/skills",
            repo_root / ".cursor" / "skills" / slug / "SKILL.md",
        ),
        ("cortex/agent-skills", cortex_sot),
        (
            "docs/agent-guides/rules",
            repo_root / "docs/agent-guides/rules" / f"{slug}.md",
        ),
    ]
    if docs_defer_cortex:
        candidates = [c for c in candidates if c[0] != "docs/agent-guides/rules"]
    for label, path in candidates:
        if path.is_file():
            return path, label
    searched = ", ".join(str(p) for _, p in candidates)
    raise FileNotFoundError(f"no SOT for {slug!r} — searched: {searched}")


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
