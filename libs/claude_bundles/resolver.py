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

# Trimmed 2026-07-11 per todo:skill-pool-dedupe judgment (agent-bus:4891) + dual-lead
# correction: universal mis-bucketed D slugs stay on UI; matter/_D cleared from standing
# Claude.ai catalog; demote_ui_only / merge / retire removed from this allowlist.
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
    "consensus-steelman-posture",
    "cheap-recon-before-escalation",
    "task-grouping-discipline",
    "planning-promotion-ladder",
    "required-skills-pickup",
    "web-skill-body-activation",
    "handoff-pickup",
    "implement-todo",
    "friction-review",
    "model-tier-awareness-web",
    "session-close-audit",
    "lead-seat-boot",
    "agent-guidance-writing",
    "skill-document-writing",
    "git-posture",
    "session-close-kernel",
    "session-close-transcript",
    "session-close-handoff",
    "session-close-reflective-journal",
    "cortex-entity-restructure",
    "handoff-packet-authoring",
    "handoff-prompt-authoring",
    "implementation-plan-workflow",
    "multi-model-review",
    "web-generate-substrate",
    "web-transcript-preprocessing",
    "email-tool-dispatch",
    "email-bridge-mailbox",
    "architecture-invariants",
    "corpus-grounded-skill-authoring",
    "todo-lifecycle",
    "orchestrator-workflow",
    "pre-deploy-gate-discipline",
    "overhaul-program",
    # Dual-lead correction — universal procedure reclassed out of retired _D
    "financial-reasoning",
    "named-entity-verification-gate",
    "document-review-timeline-linkage-audit",
]

# Matter / domain playbooks — out of standing Claude.ai Customize catalog
# (decision:skill-guidance-universal-procedure-only). Bodies remain under
# .cursor/skills/ for Cursor + case-scoped attach. Allowlist→denylist inversion
# (todo:claude-bundle-slugs-retire) still deferred.
_CLAUDE_BUNDLE_D: list[str] = []

CLAUDE_BUNDLE_SLUGS: list[str] = _CLAUDE_BUNDLE_U + _CLAUDE_BUNDLE_E + _CLAUDE_BUNDLE_D

# IDE-authored SOT under .cursor/skills/ (authoritative body, not a defer stub).
WORKSPACE_SOT_SLUGS: frozenset[str] = frozenset(
    {
        "add-mcp-tool",
        "produce-uml",
    }
)

# Indexed for cursor hardlink but excluded from Claude.ai standing catalog
# (demote_ui_only + matter case_document). Merged/retired slugs are NOT listed —
# their SOT becomes a RETIRED/MERGED stub and drops from CURSOR_INDEXED.
CURSOR_ONLY_SLUGS: list[str] = [
    # demote_ui_only (E) — Cursor + case attach; ¬ Claude.ai Customize
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
    # matter case_document — Cursor + case attach; ¬ standing Claude.ai
    "tax",
    "w2-ingestion",
    "legal-opinion-corpus-ingestion",
    "crypto-trading-research",
    "case-evidence-retrieval",
    "lawyer-stance",
    "psych-framework-counsel",
]

# Back-compat alias (removed next commit window).
CURSOR_SOT_DIRECT_SLUGS: list[str] = CURSOR_ONLY_SLUGS

# Single rule: every indexed cursor skill hardlinks to authoritative SOT.
CURSOR_INDEXED_SLUGS: list[str] = list(
    dict.fromkeys([*CLAUDE_BUNDLE_SLUGS, *CURSOR_ONLY_SLUGS])
)

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
    # cortex mirror severed — D3 Phase 1b, thread 4559
    return frozenset()


def resolve_sot(slug: str, repo_root: Path) -> tuple[Path, str]:
    """Return the first existing SOT path and a short root label for reporting."""
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
