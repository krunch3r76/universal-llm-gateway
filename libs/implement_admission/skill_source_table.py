"""Build-time generated canonical skill slug → source_uri table (D1).

Hot paths (packet render, boot, materialize) read this table only — never live
``entity_get``. Generation-time validation and ``skill_table_freshness`` compare
against live Cortex entities (F1).
"""

from __future__ import annotations

import json
from typing import Final

from cortex_store.guidance_entity import entity_slug_from_id

# fmt: off
TEMPLATE_VERSION: Final[str] = "1"

# slug synonyms → canonical table key (``entity_slug_from_id`` output may differ)
CANONICAL_SLUG_ALIASES: Final[dict[str, str]] = {
    "session-close-kernel": "session-close",
}

# Generated from live Cortex entities — prefer substantiated ``rule:`` ``source_uri``.
CANONICAL_SKILL_SOURCE_URIS: Final[dict[str, str]] = {
    "add-mcp-tool": "workspaces://universal-llm-gateway/.cursor/skills/add-mcp-tool/SKILL.md",
    "advisor-timing": "docs/agent-guides/rules/advisor-timing.md",
    "agent-bus-discipline": "workspaces://universal-llm-gateway/.cursor/skills/agent-bus-discipline/SKILL.md",
    "agent-bus-multitask": "workspaces://universal-llm-gateway/.cursor/skills/agent-bus-multitask/SKILL.md",
    "agent-guidance-writing": "workspaces://universal-llm-gateway/.cursor/skills/agent-guidance-writing/SKILL.md",
    "architecture-invariants": "workspaces://universal-llm-gateway/.cursor/skills/architecture-invariants/SKILL.md",
    "auditor-validatable-confidence": "workspaces://universal-llm-gateway/.cursor/skills/auditor-validatable-confidence/SKILL.md",
    "boot-execution-discipline": "workspaces://universal-llm-gateway/.cursor/skills/boot-execution-discipline/SKILL.md",
    "build-pipeline": "workspaces://universal-llm-gateway/.cursor/skills/build-pipeline/SKILL.md",
    "case-evidence-retrieval": "workspaces://universal-llm-gateway/.cursor/skills/case-evidence-retrieval/SKILL.md",
    "cheap-recon-before-escalation": "workspaces://universal-llm-gateway/.cursor/skills/cheap-recon-before-escalation/SKILL.md",
    "claude-ai-bundle-sync": "workspaces://universal-llm-gateway/.cursor/skills/claude-ai-bundle-sync/SKILL.md",
    "commit-and-git-scope": "workspaces://universal-llm-gateway/.cursor/skills/commit-and-git-scope/SKILL.md",
    "completion-provenance-discipline": "workspaces://universal-llm-gateway/.cursor/skills/completion-provenance-discipline/SKILL.md",
    "consensus-steelman-posture": "workspaces://universal-llm-gateway/.cursor/skills/consensus-steelman-posture/SKILL.md",
    "consult-routing": "workspaces://universal-llm-gateway/.cursor/skills/consult-routing/SKILL.md",
    "corpus-cross-reference-discipline": "workspaces://universal-llm-gateway/.cursor/skills/corpus-cross-reference-discipline/SKILL.md",
    "corpus-grounded-skill-authoring": "workspaces://universal-llm-gateway/.cursor/skills/corpus-grounded-skill-authoring/SKILL.md",
    "corpus-map-authoring": "workspaces://universal-llm-gateway/.cursor/skills/corpus-map-authoring/SKILL.md",
    "cortex": "workspaces://universal-llm-gateway/.cursor/skills/cortex/SKILL.md",
    "cortex-entity-restructure": "workspaces://universal-llm-gateway/.cursor/skills/cortex-entity-restructure/SKILL.md",
    "cortex-orientation": "workspaces://universal-llm-gateway/.cursor/skills/cortex-orientation/SKILL.md",
    "cortex-provenance-discipline": "workspaces://universal-llm-gateway/.cursor/skills/cortex-provenance-discipline/SKILL.md",
    "cortex-v24-implementation-arc": "workspaces://universal-llm-gateway/.cursor/skills/cortex-v24-implementation-arc/SKILL.md",
    "crypto-trading-research": "workspaces://universal-llm-gateway/.cursor/skills/crypto-trading-research/SKILL.md",
    "cursor-rule-authoring": "workspaces://universal-llm-gateway/.cursor/skills/cursor-rule-authoring/SKILL.md",
    "cursor-sdk-instruction-standard": "workspaces://universal-llm-gateway/.cursor/skills/cursor-sdk-instruction-standard/SKILL.md",
    "debug-with-events": "workspaces://universal-llm-gateway/.cursor/skills/debug-with-events/SKILL.md",
    "descriptor-authoring-discipline": "workspaces://universal-llm-gateway/.cursor/skills/descriptor-authoring-discipline/SKILL.md",
    "dispatch-prompt-house-style": "workspaces://universal-llm-gateway/.cursor/skills/dispatch-prompt-house-style/SKILL.md",
    "dispatch-shape": "workspaces://universal-llm-gateway/.cursor/skills/dispatch-shape/SKILL.md",
    "dispatch-workflow": "workspaces://universal-llm-gateway/.cursor/skills/dispatch-workflow/SKILL.md",
    "document-ingestion": "workspaces://universal-llm-gateway/.cursor/skills/document-ingestion/SKILL.md",
    "document-lifecycle-tracking": "workspaces://universal-llm-gateway/.cursor/skills/document-lifecycle-tracking/SKILL.md",
    "document-review-timeline-linkage-audit": "workspaces://universal-llm-gateway/.cursor/skills/document-review-timeline-linkage-audit/SKILL.md",
    "docx-ingestion": "workspaces://universal-llm-gateway/.cursor/skills/docx-ingestion/SKILL.md",
    "email-bridge-mailbox": "workspaces://universal-llm-gateway/.cursor/skills/email-bridge-mailbox/SKILL.md",
    "email-tool-dispatch": "workspaces://universal-llm-gateway/.cursor/skills/email-tool-dispatch/SKILL.md",
    "engagement-stance": "workspaces://universal-llm-gateway/.cursor/skills/engagement-stance/SKILL.md",
    "enrichment-quality-discipline": "workspaces://universal-llm-gateway/.cursor/skills/enrichment-quality-discipline/SKILL.md",
    "entity-creation-discipline": "workspaces://universal-llm-gateway/.cursor/skills/entity-creation-discipline/SKILL.md",
    "entity-lifecycle-discipline": "workspaces://universal-llm-gateway/.cursor/skills/entity-lifecycle-discipline/SKILL.md",
    "evidence-review-discipline": "workspaces://universal-llm-gateway/.cursor/skills/evidence-review-discipline/SKILL.md",
    "financial-reasoning": "workspaces://universal-llm-gateway/.cursor/skills/financial-reasoning/SKILL.md",
    "friction-review": "workspaces://universal-llm-gateway/.cursor/skills/friction-review/SKILL.md",
    "frontier-model-instructions": "workspaces://universal-llm-gateway/.cursor/skills/frontier-model-instructions/SKILL.md",
    "frontier-reasoning-discipline": "workspaces://universal-llm-gateway/.cursor/skills/frontier-reasoning-discipline/SKILL.md",
    "fs": "workspaces://universal-llm-gateway/.cursor/skills/fs/SKILL.md",
    "git-posture": "workspaces://universal-llm-gateway/.cursor/skills/git-posture/SKILL.md",
    "handoff-packet-authoring": "workspaces://universal-llm-gateway/.cursor/skills/handoff-packet-authoring/SKILL.md",
    "handoff-pickup": "docs/agent-guides/rules/handoff-pickup.md",
    "handoff-prompt-authoring": "workspaces://universal-llm-gateway/.cursor/skills/handoff-prompt-authoring/SKILL.md",
    "image-video-generation": "workspaces://universal-llm-gateway/.cursor/skills/image-video-generation/SKILL.md",
    "implement-todo": "workspaces://universal-llm-gateway/.cursor/skills/implement-todo/SKILL.md",
    "implement-work-item": "workspaces://universal-llm-gateway/.cursor/skills/implement-work-item/SKILL.md",
    "implementation-plan-workflow": "workspaces://universal-llm-gateway/.cursor/skills/implementation-plan-workflow/SKILL.md",
    "investigation-economy": "workspaces://universal-llm-gateway/.cursor/skills/investigation-economy/SKILL.md",
    "jupiter-browser-via-mcp": "workspaces://universal-llm-gateway/.cursor/skills/jupiter-browser-via-mcp/SKILL.md",
    "lawyer-stance": "workspaces://universal-llm-gateway/.cursor/skills/lawyer-stance/SKILL.md",
    "lead-agent-git-integration": "workspaces://universal-llm-gateway/.cursor/skills/lead-agent-git-integration/SKILL.md",
    "lead-seat-boot": "workspaces://universal-llm-gateway/.cursor/skills/lead-seat-boot/SKILL.md",
    "legal-opinion-corpus-ingestion": "workspaces://universal-llm-gateway/.cursor/skills/legal-opinion-corpus-ingestion/SKILL.md",
    "markdown-navigation": "workspaces://universal-llm-gateway/.cursor/skills/markdown-navigation/SKILL.md",
    "mcp-surface-change": "workspaces://universal-llm-gateway/.cursor/skills/mcp-surface-change/SKILL.md",
    "mcp-tool-loop-trace-matrix": "workspaces://universal-llm-gateway/.cursor/skills/mcp-tool-loop-trace-matrix/SKILL.md",
    "model-tier-awareness-web": "workspaces://universal-llm-gateway/.cursor/skills/model-tier-awareness-web/SKILL.md",
    "modularize-discipline": "workspaces://universal-llm-gateway/.cursor/skills/modularize-discipline/SKILL.md",
    "multi-model-review": "workspaces://universal-llm-gateway/.cursor/skills/multi-model-review/SKILL.md",
    "named-entity-verification-gate": "workspaces://universal-llm-gateway/.cursor/skills/named-entity-verification-gate/SKILL.md",
    "no-silent-inference": "workspaces://universal-llm-gateway/.cursor/skills/no-silent-inference/SKILL.md",
    "operator-posture": "workspaces://universal-llm-gateway/.cursor/skills/operator-posture/SKILL.md",
    "orchestrator-core": "workspaces://universal-llm-gateway/.cursor/skills/orchestrator-core/SKILL.md",
    "orchestrator-workflow": "workspaces://universal-llm-gateway/.cursor/skills/orchestrator-workflow/SKILL.md",
    "overhaul-program": "workspaces://universal-llm-gateway/.cursor/skills/overhaul-program/SKILL.md",
    "pipeline-substrate-capabilities": "workspaces://universal-llm-gateway/.cursor/skills/pipeline-substrate-capabilities/SKILL.md",
    "planning-promotion-ladder": "workspaces://universal-llm-gateway/.cursor/skills/planning-promotion-ladder/SKILL.md",
    "pre-deploy-gate-discipline": "workspaces://universal-llm-gateway/.cursor/skills/pre-deploy-gate-discipline/SKILL.md",
    "produce-uml": "workspaces://universal-llm-gateway/.cursor/skills/produce-uml/SKILL.md",
    "prose-discipline": "workspaces://universal-llm-gateway/.cursor/skills/prose-discipline/SKILL.md",
    "provenance-granularity": "workspaces://universal-llm-gateway/.cursor/skills/provenance-granularity/SKILL.md",
    "psych-framework-counsel": "workspaces://universal-llm-gateway/.cursor/skills/psych-framework-counsel/SKILL.md",
    "rag-canonical-reference-reminder": "workspaces://universal-llm-gateway/.cursor/skills/rag-canonical-reference-reminder/SKILL.md",
    "refine-pipeline": "workspaces://universal-llm-gateway/.cursor/skills/refine-pipeline/SKILL.md",
    "required-skills-pickup": "workspaces://universal-llm-gateway/.cursor/skills/required-skills-pickup/SKILL.md",
    "research-article-ingest": "workspaces://universal-llm-gateway/.cursor/skills/research-article-ingest/SKILL.md",
    "research-article-search": "workspaces://universal-llm-gateway/.cursor/skills/research-article-search/SKILL.md",
    "review-task-guidance": "workspaces://universal-llm-gateway/.cursor/skills/review-task-guidance/SKILL.md",
    "service-lifecycle": "workspaces://universal-llm-gateway/.cursor/skills/service-lifecycle/SKILL.md",
    "session-close": "workspaces://universal-llm-gateway/.cursor/skills/session-close-kernel/SKILL.md",
    "session-close-audit": "workspaces://universal-llm-gateway/.cursor/skills/session-close-audit/SKILL.md",
    "session-close-handoff": "workspaces://universal-llm-gateway/.cursor/skills/session-close-handoff/SKILL.md",
    "session-close-reflective-journal": "workspaces://universal-llm-gateway/.cursor/skills/session-close-reflective-journal/SKILL.md",
    "session-close-transcript": "workspaces://universal-llm-gateway/.cursor/skills/session-close-transcript/SKILL.md",
    "skill-document-writing": "workspaces://universal-llm-gateway/.cursor/skills/skill-document-writing/SKILL.md",
    "srm": "workspaces://universal-llm-gateway/.cursor/skills/srm/SKILL.md",
    "subgraph-render": "workspaces://universal-llm-gateway/.cursor/skills/subgraph-render/SKILL.md",
    "task-grouping-discipline": "workspaces://universal-llm-gateway/.cursor/skills/task-grouping-discipline/SKILL.md",
    "tax": "workspaces://universal-llm-gateway/.cursor/skills/tax/SKILL.md",
    "thirdparty-api-mirror": "workspaces://universal-llm-gateway/.cursor/skills/thirdparty-api-mirror/SKILL.md",
    "todo-lifecycle": "docs/agent-guides/rules/todo-lifecycle.md",
    "ulg-architecture": "workspaces://universal-llm-gateway/.cursor/skills/ulg-architecture/SKILL.md",
    "w2-ingestion": "workspaces://universal-llm-gateway/.cursor/skills/w2-ingestion/SKILL.md",
    "web-generate-substrate": "workspaces://universal-llm-gateway/.cursor/skills/web-generate-substrate/SKILL.md",
    "web-transcript-preprocessing": "workspaces://universal-llm-gateway/.cursor/skills/web-transcript-preprocessing/SKILL.md",
    "writing-discipline-outbound": "workspaces://universal-llm-gateway/.cursor/skills/writing-discipline-outbound/SKILL.md",
}

TABLE_DIGEST: Final[str] = "sha256:654b3d399243924ec1272d6a33a0daabbb3046679ce31df80f8e66271e1cf662"
# fmt: on


class SkillSourceResolveError(LookupError):
    """Canonical slug absent from the committed resolver table."""


def canonical_table_key(slug_or_entity_id: str) -> str:
    """Normalize any entity id or bare slug to a canonical table key."""
    raw = slug_or_entity_id.strip()
    slug = entity_slug_from_id(raw) if ":" in raw else raw
    return CANONICAL_SLUG_ALIASES.get(slug, slug)


def canonical_agent_skill_id(slug_or_entity_id: str) -> str:
    """Double-load exclusion key — always ``agent_skill:{canonical_slug}``."""
    return f"agent_skill:{canonical_table_key(slug_or_entity_id)}"


def resolve_canonical_source_uri(slug_or_entity_id: str) -> str:
    """Map slug/entity id → ``source_uri`` via the committed table (D1)."""
    key = canonical_table_key(slug_or_entity_id)
    uri = CANONICAL_SKILL_SOURCE_URIS.get(key)
    if not uri:
        raise SkillSourceResolveError(
            f"canonical slug {key!r} absent from skill source table "
            f"(template_version={TEMPLATE_VERSION})"
        )
    return uri


def table_bytes_for_digest() -> bytes:
    """Stable serialization for determinism / freshness probes."""
    return json.dumps(
        CANONICAL_SKILL_SOURCE_URIS, sort_keys=True, separators=(",", ":")
    ).encode()
