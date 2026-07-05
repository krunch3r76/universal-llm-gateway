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
    "ulg-architecture": "ulg-architecture_ulg",
}

# Generated from live Cortex entities — prefer substantiated ``rule:`` ``source_uri``.
CANONICAL_SKILL_SOURCE_URIS: Final[dict[str, str]] = {
    "add-mcp-tool": "workspaces://universal-llm-gateway/.cursor/skills/add-mcp-tool/SKILL.md",
    "add-mcp-tool_ulg": "workspaces://universal-llm-gateway/.cursor/skills/add-mcp-tool/SKILL.md",
    "advisor-timing": "agent-skills/advisor-timing.md",
    "agent-bus-discipline": "agent-skills/agent-bus-discipline.md",
    "agent-bus-multitask": "agent-skills/agent-bus-multitask.md",
    "agent-guidance-writing": "workspaces://universal-llm-gateway/docs/agent-guides/skills/agent-guidance-writing.md",
    "agent-identity-signoff": "agent-skills/agent-identity-signoff.md",
    "architecture-invariants": "workspaces://universal-llm-gateway/docs/agent-guides/skills/architecture-invariants.md",
    "auditor-validatable-confidence": "agent-skills/auditor-validatable-confidence.md",
    "boot-execution-discipline": "agent-skills/boot-execution-discipline.md",
    "build-pipeline": "workspaces://universal-llm-gateway/docs/agent-guides/skills/build-pipeline/SKILL.md",
    "build-pipeline_ulg": "workspaces://universal-llm-gateway/docs/agent-guides/skills/build-pipeline/SKILL.md",
    "case-evidence-retrieval": "agent-skills/case-evidence-retrieval.md",
    "cheap-recon-before-escalation": "workspaces://universal-llm-gateway/.cursor/skills/cheap-recon-before-escalation/SKILL.md",
    "claudeburst-shadow-ops": "agent-skills/claudeburst-shadow-ops.md",
    "commit-and-git-scope": "workspaces://universal-llm-gateway/.cursor/skills/commit-and-git-scope/SKILL.md",
    "completion-provenance-discipline": "agent-skills/completion-provenance-discipline.md",
    "consensus-steelman-posture": "agent-skills/consensus-steelman-posture.md",
    "consult-routing": "agent-skills/consult-routing.md",
    "corpus-cross-reference-discipline": "workspaces://universal-llm-gateway/.cursor/skills/corpus-cross-reference-discipline/SKILL.md",
    "corpus-grounded-skill-authoring": "workspaces://universal-llm-gateway/.cursor/skills/corpus-grounded-skill-authoring/SKILL.md",
    "corpus-map-authoring": "workspaces://universal-llm-gateway/.cursor/skills/corpus-map-authoring/SKILL.md",
    "cortex": "workspaces://universal-llm-gateway/docs/agent-guides/skills/cortex.md",
    "cortex-entity-restructure": "workspaces://universal-llm-gateway/.cursor/skills/cortex-entity-restructure/SKILL.md",
    "cortex-orientation": "agent-skills/cortex-orientation.md",
    "cortex-provenance-discipline": "agent-skills/cortex-provenance-discipline.md",
    "cortex-v24-implementation-arc": "agent-skills/cortex-v24-implementation-arc.md",
    "crypto-trading-research": "agent-skills/crypto-trading-research.md",
    "cursor-rule-authoring": "agent-skills/cursor-rule-authoring.md",
    "cursor-sdk-instruction-standard": "workspaces://universal-llm-gateway/docs/agent-guides/skills/cursor-sdk-instruction-standard.md",
    "debug-with-events": "agent-skills/debug-with-events.md",
    "debug-with-events_ulg": "workspaces://universal-llm-gateway/.cursor/skills/debug-with-events/SKILL.md",
    "descriptor-authoring-discipline": "workspaces://universal-llm-gateway/docs/agent-guides/skills/descriptor-authoring-discipline.md",
    "dispatch-prompt-house-style": "agent-skills/dispatch-prompt-house-style.md",
    "dispatch-shape": "agent-skills/dispatch-shape.md",
    "dispatch-workflow": "workspaces://universal-llm-gateway/.cursor/skills/dispatch-workflow/SKILL.md",
    "document-critique-timeline-linkage-audit": "agent-bus:925",
    "document-ingestion": "agent-skills/document-ingestion.md",
    "document-lifecycle-tracking": "agent-skills/document-lifecycle-tracking.md",
    "document-review-timeline-linkage-audit": "agent-skills/document-review-timeline-linkage-audit.md",
    "docx-ingestion": "workspaces://universal-llm-gateway/.cursor/skills/docx-ingestion/SKILL.md",
    "email-bridge-mailbox": "agent-skills/email-bridge-mailbox.md",
    "email-tool-dispatch": "agent-skills/email-tool-dispatch.md",
    "engagement-stance": "agent-skills/engagement-stance.md",
    "enrichment-quality-discipline": "agent-skills/enrichment-quality-discipline.md",
    "entity-creation-discipline": "agent-skills/entity-creation-discipline.md",
    "entity-lifecycle-discipline": "agent-skills/entity-lifecycle-discipline.md",
    "evidence-review-discipline": "agent-skills/evidence-review-discipline.md",
    "financial-reasoning": "agent-skills/financial-reasoning.md",
    "friction-review": "workspaces://universal-llm-gateway/docs/agent-guides/skills/friction-review.md",
    "frontier-model-instructions": "agent-skills/frontier-model-instructions.md",
    "frontier-reasoning-discipline": "agent-skills/frontier-reasoning-discipline.md",
    "fs": "agent-skills/fs.md",
    "git-posture": "workspaces://universal-llm-gateway/docs/agent-guides/skills/git-posture.md",
    "handoff-packet-authoring": "workspaces://universal-llm-gateway/docs/agent-guides/skills/handoff-packet-authoring.md",
    "handoff-pickup": "agent-skills/handoff-pickup.md",
    "handoff-prompt-authoring": "workspaces://universal-llm-gateway/.cursor/skills/handoff-prompt-authoring/SKILL.md",
    "image-video-generation": "workspaces://universal-llm-gateway/.cursor/skills/image-video-generation/SKILL.md",
    "implement-todo": "agent-skills/implement-todo.md",
    "implement-work-item": "workspaces://universal-llm-gateway/.cursor/skills/implement-work-item/SKILL.md",
    "implementation-plan-workflow": "workspaces://universal-llm-gateway/.cursor/skills/implementation-plan-workflow/SKILL.md",
    "investigation-economy": "agent-skills/investigation-economy.md",
    "jupiter-browser-via-mcp": "agent-skills/jupiter-browser-via-mcp.md",
    "lawyer-stance": "agent-skills/lawyer-stance.md",
    "lead-agent-git-integration": "agent-skills/lead-agent-git-integration.md",
    "lead-seat-boot": "workspaces://universal-llm-gateway/.cursor/skills/lead-seat-boot/SKILL.md",
    "legal-opinion-corpus-ingestion": "agent-skills/legal-opinion-corpus-ingestion.md",
    "markdown-navigation": "agent-skills/markdown-navigation.md",
    "matter-discipline-pattern": "workspaces://universal-llm-gateway/.cursor/skills/matter-discipline-pattern/SKILL.md",
    "mcp-surface-change": "agent-skills/mcp-surface-change.md",
    "mcp-tool-loop-trace-matrix": "workspaces://universal-llm-gateway/.cursor/skills/mcp-tool-loop-trace-matrix/SKILL.md",
    "mcp-tool-loop-trace-matrix_ulg": "agent-skills/mcp-tool-loop-trace-matrix.md",
    "model-tier-awareness-web": "agent-skills/model-tier-awareness-web.md",
    "modularize-discipline": "agent-skills/modularize-discipline.md",
    "multi-model-review": "workspaces://universal-llm-gateway/.cursor/skills/multi-model-review/SKILL.md",
    "named-entity-verification-gate": "agent-skills/named-entity-verification-gate.md",
    "no-silent-inference": "agent-skills/no-silent-inference.md",
    "operator-posture": "agent-skills/operator-posture.md",
    "orchestrator-core": "agent-skills/orchestrator-core.md",
    "orchestrator-workflow": "agent-skills/orchestrator-workflow.md",
    "pipeline-substrate-capabilities": "workspaces://universal-llm-gateway/.cursor/skills/pipeline-substrate-capabilities/SKILL.md",
    "planning-promotion-ladder": "agent-skills/planning-promotion-ladder.md",
    "pre-deploy-gate-discipline": "workspaces://universal-llm-gateway/.cursor/skills/pre-deploy-gate-discipline/SKILL.md",
    "produce-uml": "workspaces://universal-llm-gateway/.cursor/skills/produce-uml/SKILL.md",
    "prose-discipline": "agent-skills/prose-discipline.md",
    "provenance-granularity": "workspaces://universal-llm-gateway/.cursor/skills/provenance-granularity/SKILL.md",
    "rag-canonical-reference-reminder": "workspaces://universal-llm-gateway/.cursor/skills/rag-canonical-reference-reminder/SKILL.md",
    "refine-pipeline": "workspaces://universal-llm-gateway/docs/agent-guides/skills/refine-pipeline.md",
    "refine-pipeline_ulg": "workspaces://universal-llm-gateway/docs/agent-guides/skills/refine-pipeline.md",
    "required-skills-pickup": "workspaces://universal-llm-gateway/.cursor/skills/required-skills-pickup/SKILL.md",
    "research-article-ingest": "workspaces://universal-llm-gateway/.cursor/skills/research-article-ingest/SKILL.md",
    "research-article-search": "workspaces://universal-llm-gateway/.cursor/skills/research-article-search/SKILL.md",
    "review-protocol-mandatory-chronology-verification": "agent-skills/review-protocol-mandatory-chronology-verification.md",
    "review-task-guidance": "workspaces://universal-llm-gateway/.cursor/skills/review-task-guidance/SKILL.md",
    "service-lifecycle": "agent-skills/service-lifecycle.md",
    "session-close": "agent-skills/session-close-kernel.md",
    "session-close-audit": "agent-skills/session-close-audit.md",
    "session-close-handoff": "workspaces://universal-llm-gateway/.cursor/skills/session-close-handoff/SKILL.md",
    "session-close-reflective-journal": "workspaces://universal-llm-gateway/.cursor/skills/session-close-reflective-journal/SKILL.md",
    "session-close-transcript": "workspaces://universal-llm-gateway/.cursor/skills/session-close-transcript/SKILL.md",
    "skill-document-writing": "agent-skills/skill-document-writing.md",
    "skill-suggest-utilization": "workspaces://universal-llm-gateway/docs/agent-guides/skills/skill-suggest-utilization.md",
    "srm": "agent-skills/srm.md",
    "subgraph-render": "workspaces://universal-llm-gateway/.cursor/skills/subgraph-render/SKILL.md",
    "task-grouping-discipline": "agent-skills/task-grouping-discipline.md",
    "tax": "agent-skills/tax.md",
    "thirdparty-api-mirror": "workspaces://universal-llm-gateway/.cursor/skills/thirdparty-api-mirror/SKILL.md",
    "todo-lifecycle": "docs/agent-guides/rules/todo-lifecycle.md",
    "ulg-architecture_ulg": "workspaces://universal-llm-gateway/docs/agent-guides/skills/ulg-architecture.md",
    "w2-ingestion": "agent-skills/w2-ingestion.md",
    "web-generate-substrate": "workspaces://universal-llm-gateway/.cursor/skills/web-generate-substrate/SKILL.md",
    "web-transcript-preprocessing": "workspaces://universal-llm-gateway/.cursor/skills/web-transcript-preprocessing/SKILL.md",
}

TABLE_DIGEST: Final[str] = "sha256:8c59795d74c53191dd0df6604d2940344800838c6ebc32c4131496b9c34606c1"
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
