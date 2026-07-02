"""SOT resolution and inline transform for claude.ai skill bundles."""

from __future__ import annotations

import json
import re
from pathlib import Path

# claude.ai bundle = all CURSOR_INDEXED skills except matter playbooks pending retirement
# (see CURSOR_ONLY_SLUGS). Cursor hardlinks cover full CURSOR_INDEXED union.
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
    "lead-seat-boot",
    "cursor-sdk-instruction-standard",
    "agent-guidance-writing",
    "skill-document-writing",
    "commit-and-git-scope",
    "git-posture",
    "xai-mcp-calling-shape",
    "session-close-kernel",
    "session-close-transcript",
    "session-close-handoff",
    "session-close-reflective-journal",
    "cortex-entity-restructure",
    "subgraph-render",
    "handoff-packet-authoring",
    "handoff-prompt-authoring",
    "implementation-plan-workflow",
    "implement-work-item",
    "multi-model-review",
    "agent-bus-multitask",
    "research-article-ingest",
    "research-article-search",
    "document-ingestion",
    "docx-ingestion",
    "image-video-generation",
    "thirdparty-api-mirror",
    "web-generate-substrate",
    "web-transcript-preprocessing",
    "review-task-guidance",
    "email-tool-dispatch",
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
]

CLAUDE_BUNDLE_SLUGS: list[str] = _CLAUDE_BUNDLE_U + _CLAUDE_BUNDLE_E

# IDE-authored SOT under .cursor/skills/ (authoritative body, not a defer stub).
WORKSPACE_SOT_SLUGS: frozenset[str] = frozenset(
    {
        "add-mcp-tool",
        "produce-uml",
    }
)

# Indexed for cursor hardlink but excluded from .claude render (matter playbook — retiring).
CURSOR_ONLY_SLUGS: list[str] = [
    "hei-application-discipline",
]

# Back-compat alias (removed next commit window).
CURSOR_SOT_DIRECT_SLUGS: list[str] = CURSOR_ONLY_SLUGS

# Single rule: every indexed cursor skill hardlinks to authoritative SOT.
CURSOR_INDEXED_SLUGS: list[str] = list(
    dict.fromkeys([*CLAUDE_BUNDLE_SLUGS, *CURSOR_ONLY_SLUGS])
)

# SOT must live under cortex (not git-whitelisted docs) — personal/domain skills.
CORTEX_SOT_ONLY_SLUGS: frozenset[str] = frozenset(
    {
        "hei-application-discipline",
        "chase-escrow-discipline",
        "chase-escrow-statement-ingestion",
        "boe19p-appeal-discipline",
        "case-evidence-retrieval",
        "crypto-trading-research",
        "document-ingestion",
        "document-lifecycle-tracking",
        "email-tool-dispatch",
        "engagement-stance",
        "financial-reasoning",
        "flintridge-case-navigation",
        "lawyer-stance",
        "legal-opinion-corpus-ingestion",
        "srm",
        "tax",
        "w2-ingestion",
    }
)

CORTEX_SOT_ROOT = Path("/mnt/torus/mcp-data/files/agent-skills")

_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---", re.DOTALL)
_SOT_LINE_RE = re.compile(r"^\*\*SOT")
_SOURCE_LINE_RE = re.compile(r"^\*\*Source:\*\*")
_GENERATED_COMMENT_RE = re.compile(r"GENERATED\s*[—-]\s*DO NOT EDIT")


def _docs_defers_to_cortex(docs_text: str) -> bool:
    """True when docs/agent-guides/skills is a pointer stub, not authoritative body."""
    if "agent-skills/" not in docs_text:
        return False
    defer_markers = (
        "Do not maintain a second long-form copy",
        "Do not duplicate the cortex playbook",
        "the cortex file owns",
    )
    return any(marker in docs_text for marker in defer_markers)


def _cortex_mount_missing() -> bool:
    return not CORTEX_SOT_ROOT.is_dir()


def resolve_sot(slug: str, repo_root: Path) -> tuple[Path, str]:
    """Return the first existing SOT path and a short root label for reporting."""
    if _cortex_mount_missing() and slug in CORTEX_SOT_ONLY_SLUGS:
        raise FileNotFoundError(
            f"cortex SOT mount missing ({CORTEX_SOT_ROOT}) — cannot resolve {slug!r}"
        )
    docs_skills = repo_root / "docs/agent-guides/skills" / f"{slug}.md"
    cortex_sot = CORTEX_SOT_ROOT / f"{slug}.md"
    docs_defer_cortex = (
        docs_skills.is_file()
        and _docs_defers_to_cortex(docs_skills.read_text(encoding="utf-8"))
    )
    if docs_skills.is_file() and cortex_sot.is_file() and docs_defer_cortex:
        return cortex_sot, "cortex/agent-skills"
    if docs_defer_cortex and not cortex_sot.is_file():
        raise FileNotFoundError(
            f"no SOT for {slug!r} — docs stub defers to missing {cortex_sot}"
        )
    candidates: list[tuple[str, Path]] = [
        ("docs/agent-guides/skills", docs_skills),
        (
            "docs/agent-guides/skills",
            repo_root / "docs/agent-guides/skills" / slug / "SKILL.md",
        ),
        (
            "docs/agent-guides/rules",
            repo_root / "docs/agent-guides/rules" / f"{slug}.md",
        ),
        ("cortex/agent-skills", cortex_sot),
    ]
    if slug in WORKSPACE_SOT_SLUGS or slug not in CURSOR_INDEXED_SLUGS:
        candidates.append(
            (
                ".cursor/skills",
                repo_root / ".cursor" / "skills" / slug / "SKILL.md",
            )
        )
    if docs_defer_cortex:
        candidates = [c for c in candidates if c[0] != "docs/agent-guides/rules"]
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
