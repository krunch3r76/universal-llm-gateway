"""Module-level constants for skill ingest and stub generation tooling."""

from __future__ import annotations

import re
from pathlib import Path

_CURSOR_SKILLS_SOT_RE = re.compile(
    r"SOT:[\s`*]*"
    r"(?:workspaces://universal-llm-gateway/\.cursor/skills/"
    r"|\.cursor/skills/"
    r"|cortex://agent-skills/"
    r'|fs\(sandbox="cortex",\s*op="read",\s*path="agent-skills/)'
    r"([A-Za-z0-9_-]+)(?:/SKILL\.md|\.md)?"
)
# Legacy alias — importers use _CORTEX_SOT_RE name; pattern now resolves .cursor/skills SOT.
_CORTEX_SOT_RE = _CURSOR_SKILLS_SOT_RE
_SUPPRESSED = frozenset({"deprecated", "retired"})
# todo-lifecycle = third-home docs/agent-guides/rules/todo-lifecycle.md hold-out
_SOT_DRIFT_HOLDOUTS = frozenset({"todo-lifecycle"})
# temporary — cleared by the 4559 hygiene wave
# cortex-v24 = cortex source_uri pending refresh (charter D4 residual #1)
# writing-discipline-outbound = graph-only anomaly pending D2 disposition (census anomaly 1)
_SOT_DRIFT_KNOWN_RESIDUALS = frozenset(
    {"cortex-v24-implementation-arc", "writing-discipline-outbound"}
)
_CREATE_SUPPRESSED_LIFECYCLES = frozenset({"deprecated", "retired", "merged"})
_WS = "workspaces://universal-llm-gateway"
_SYNC_SOURCE_URI = f"{_WS}/.cursor/skills/skill-document-writing/SKILL.md"
_CLAUDE_SKILLS_REL = ".claude/skills/"
_CURSOR_SKILLS_REL = ".cursor/skills/"
_INGEST_CHECK_DRIFT_HOLDOUTS = frozenset(
    {
        "handoff-packet-authoring",
        "implementation-plan-workflow",
        "multi-model-review",
        "refine-pipeline",
    }
)
_SKIP_CORTEX_SOT = frozenset({"README"})

GENERATOR_VERSION = "1.0.0"
REMEDIATION_CMD = "make skill-graph-reconcile"
MANIFEST_FILENAME = ".generated-manifest.json"
GENERATED_HEADER = (
    "GENERATED — DO NOT EDIT (stub/frontmatter from agent_skill metadata; "
    "body SOT = .cursor/skills/<slug>/SKILL.md; "
    f"regen: {REMEDIATION_CMD})"
)

RENDERER_INPUT_FIELDS: tuple[str, ...] = (
    "description",
    "trigger_match_terms",
    "related_skills",
    "references",
    "aliases",
    "source_uri",
    "paired_rule_pointer",
)

STUB_CRITICAL_FIELDS: frozenset[str] = frozenset(
    {
        "description",
        "trigger_match_terms",
        "source_uri",
        "paired_rule_pointer",
    }
)

ALLOWLIST_METADATA_KEYS: tuple[str, ...] = (
    "reason",
    "owner",
    "expiry_or_assertion_ref",
    "directionality",
    "temporary_or_structural",
)


def normalize_slug(slug: str) -> str:
    """Canonical slug normalization shared by ingest and generate."""
    return slug.strip().lower()


def slug_to_name(slug: str) -> str:
    """Derive display name from slug (shared by ingest projection and stubs)."""
    return " ".join(part.capitalize() for part in normalize_slug(slug).split("-"))


def paired_rule_exists(slug: str, repo_root: Path) -> bool:
    """True when a paired Cursor rule file exists for *slug*."""
    candidates = (
        repo_root / ".cursor" / "rules",
        repo_root.parent / ".cursor" / "rules",
    )
    names = (f"{slug}.mdc", f"{slug}-stub.mdc")
    for rules_dir in candidates:
        if not rules_dir.is_dir():
            continue
        for name in names:
            if (rules_dir / name).is_file():
                return True
    return False
