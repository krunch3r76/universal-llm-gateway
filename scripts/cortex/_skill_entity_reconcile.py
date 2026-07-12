"""Reconcile CURSOR_INDEXED slug list against live agent_skill entities."""

from __future__ import annotations

from typing import Iterable

from claude_bundles.resolver import CURSOR_INDEXED_SLUGS, LIFE_LOCAL_SLUGS

# Active cortex rows expected outside build membership (web-only, retired, etc.).
_ENTITY_NOT_INDEXED_ALLOWLIST: frozenset[str] = frozenset(
    {
        "agent-build",
        "claude-web-boot",
        "claudeburst-shadow-ops",
        "delegate-to-grok",
        "document-ocr",
        "document-review-timeline-linkage-audit",
        "docx-ingestion",
        "email-bridge-mailbox",
        "email-tool-dispatch",
        "engagement-stance",
        "grok-build-dispatch",
        "grok-web-dispatch",
        "grokbuild",
        "grokbuild-v1",
        "grokbuild-v2",
        "implement-todo",
        "jupiter-browser-via-mcp",
        "lead-agent-git-integration",
        "matter-playbook-lifecycle",
        "mode-b-web-orchestrator",
        "named-entity-verification-gate",
        "prose-discipline",
        "review-protocol-mandatory-chronology-verification",
        "skill-authoring",
        "srm",
        "superheavy-dispatch",
        # Matter/domain skills not in CURSOR_INDEXED (wave D or universal scrub pending).
        "case-evidence-retrieval",
        "crypto-trading-research",
        "financial-reasoning",
        "lawyer-stance",
        "legal-opinion-corpus-ingestion",
        "tax",
        "w2-ingestion",
        # Dropped from SHARED_SYNC / UI thin-set — active entities, not cursor-indexed.
        "agent-guidance-writing",
        "architecture-invariants",
        "auditor-validatable-confidence",
        "boot-execution-discipline",
        "cheap-recon-before-escalation",
        "claude-ai-bundle-sync",
        "claude-ai-mcp-connect",
        "corpus-grounded-skill-authoring",
        "cortex",
        "cortex-entity-restructure",
        "cortex-v24-implementation-arc",
        "dispatch-workflow",
        "enrichment-quality-discipline",
        "friction-review",
        "frontier-model-instructions",
        "git-posture",
        "handoff-packet-authoring",
        "handoff-prompt-authoring",
        "implementation-plan-workflow",
        "investigation-economy",
        "markdown-navigation",
        "modularize-discipline",
        "multi-model-review",
        "orchestrator-core",
        "orchestrator-workflow",
        "overhaul-program",
        "planning-promotion-ladder",
        "pre-deploy-gate-discipline",
        "refine-pipeline",
        "service-lifecycle",
        "session-close-handoff",
        "session-close-reflective-journal",
        "session-close-transcript",
        "skill-document-writing",
        "task-grouping-discipline",
        "todo-lifecycle",
        "web-generate-substrate",
        "web-skill-body-activation",
        "web-transcript-preprocessing",
    }
) | frozenset(LIFE_LOCAL_SLUGS)


def _fetch_active_agent_skill_slugs(client: object) -> tuple[set[str] | None, str | None]:
    from _skill_projection import _request

    status, body = _request(
        client,
        "GET",
        "/entities?type=agent_skill&limit=500&include_non_active=false",
    )
    if status != 200:
        return None, f"cortex entities GET failed: HTTP {status}"
    items = body.get("items") or body.get("entities") or []
    slugs: set[str] = set()
    for row in items:
        eid = str(row.get("id") or "")
        if eid.startswith("agent_skill:"):
            slugs.add(eid.removeprefix("agent_skill:"))
    return slugs, None


def reconcile_indexed_vs_entities(
    indexed: Iterable[str] | None = None,
    *,
    client: object | None = None,
) -> tuple[list[str], list[str], str | None]:
    """Return (indexed_missing_entity, entity_not_indexed, skip_reason)."""
    if client is None:
        return [], [], "cortex unavailable"
    entity_slugs, err = _fetch_active_agent_skill_slugs(client)
    if entity_slugs is None:
        return [], [], err
    indexed_set = set(indexed or CURSOR_INDEXED_SLUGS)
    indexed_missing = sorted(slug for slug in indexed_set if slug not in entity_slugs)
    entity_not_indexed = sorted(
        slug
        for slug in entity_slugs
        if slug not in indexed_set and slug not in _ENTITY_NOT_INDEXED_ALLOWLIST
    )
    return indexed_missing, entity_not_indexed, None


def run_entity_reconcile_check(*, client: object | None = None) -> int:
    """Print reconciliation diff; return 1 on unexpected mismatches."""
    indexed_missing, entity_not_indexed, skip = reconcile_indexed_vs_entities(
        client=client
    )
    if skip:
        print(f"INFO entity-reconcile skipped: {skip}", flush=True)
        return 0
    fail = 0
    if indexed_missing:
        print(
            "RECONCILE: indexed slugs missing agent_skill entity: "
            + ", ".join(indexed_missing),
            flush=True,
        )
        fail = 1
    if entity_not_indexed:
        print(
            "RECONCILE: active agent_skill not in CURSOR_INDEXED (not allowlisted): "
            + ", ".join(entity_not_indexed),
            flush=True,
        )
        fail = 1
    if fail == 0:
        print("OK entity-reconcile", flush=True)
    return fail
