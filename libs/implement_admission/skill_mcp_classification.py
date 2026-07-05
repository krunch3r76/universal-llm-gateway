"""Build-time generated skill MCP-predicated classification (S2.a / F2).

Hot paths read this table only — never infer from body grep at dispatch.
"""

from __future__ import annotations

import json
from typing import Final

from implement_admission.skill_source_table import canonical_table_key

TEMPLATE_VERSION: Final[str] = "1"

SKILL_MCP_PREDICATED: Final[dict[str, bool]] = {
    "README": False,
    "add-mcp-tool": True,
    "advisor-timing": True,
    "agent-bus-discipline": True,
    "agent-bus-multitask": True,
    "agent-guidance-writing": False,
    "agent-identity-signoff": False,
    "architecture-invariants": False,
    "auditor-validatable-confidence": True,
    "boot-execution-discipline": True,
    "build-pipeline": True,
    "cheap-recon-before-escalation": True,
    "claude-ai-bundle-sync": False,
    "commit-and-git-scope": False,
    "completion-provenance-discipline": False,
    "consensus-steelman-posture": True,
    "consult-routing": True,
    "corpus-cross-reference-discipline": True,
    "corpus-grounded-skill-authoring": False,
    "corpus-map-authoring": True,
    "cortex": True,
    "cortex-entity-restructure": True,
    "cortex-orientation": True,
    "cortex-provenance-discipline": False,
    "cursor-rule-authoring": False,
    "cursor-sdk-instruction-standard": False,
    "debug-with-events": True,
    "descriptor-authoring-discipline": False,
    "dispatch-shape": True,
    "dispatch-workflow": True,
    "document-ingestion": True,
    "document-lifecycle-tracking": True,
    "docx-ingestion": True,
    "email-tool-dispatch": True,
    "engagement-stance": False,
    "enrichment-quality-discipline": True,
    "entity-creation-discipline": True,
    "entity-lifecycle-discipline": False,
    "events-docs": False,
    "evidence-review-discipline": False,
    "friction-review": True,
    "frontier-model-instructions": False,
    "frontier-reasoning-discipline": False,
    "fs": True,
    "git-posture": False,
    "handler-reference": False,
    "handoff-packet-authoring": True,
    "handoff-pickup": True,
    "handoff-prompt-authoring": False,
    "image-video-generation": True,
    "implement-todo": True,
    "implement-work-item": True,
    "implementation-plan-workflow": True,
    "investigation-economy": False,
    "lead-seat-boot": True,
    "markdown-navigation": True,
    "matter-discipline-pattern": True,
    "mcp-surface-change": True,
    "mcp-tool-loop-trace-matrix": True,
    "model-lifecycle": False,
    "model-tier-awareness-web": False,
    "modularize-discipline": False,
    "multi-model-review": True,
    "no-silent-inference": False,
    "operator-posture": False,
    "orchestrator-core": False,
    "orchestrator-workflow": True,
    "overhaul-program": True,
    "pipeline-substrate-capabilities": True,
    "planning-promotion-ladder": False,
    "pre-deploy-gate-discipline": False,
    "produce-uml": False,
    "prose-discipline": False,
    "provenance-granularity": False,
    "quality-gates": False,
    "refine-pipeline": True,
    "required-skills-pickup": True,
    "research-article-ingest": True,
    "research-article-search": True,
    "review-task-guidance": False,
    "service-lifecycle": True,
    "service-ops": True,
    "session-close": True,
    "session-close-audit": True,
    "session-close-handoff": True,
    "session-close-reflective-journal": True,
    "session-close-transcript": True,
    "skill-document-writing": False,
    "skill-suggest-utilization": True,
    "srm": False,
    "subgraph-render": True,
    "task-grouping-discipline": False,
    "thirdparty-api-mirror": True,
    "todo-lifecycle": False,
    "ulg-architecture_ulg": False,
    "web-boot-lead": True,
    "web-generate-substrate": False,
    "web-transcript-preprocessing": True,
    "yaml-reference": False,
}

CLASSIFICATION_DIGEST: Final[str] = 'sha256:e383378550bd1122631806ba71b2aade92a4295086a29a5146403001fb52a201'

DISPOSITION_SOURCE_SHA256: Final[str] = 'sha256:ed1ccc90b5ca3ce57ec24ad4eb472e6fb5d625badda8b98e657f71b9948b0d8f'


class SkillClassificationMissingError(LookupError):
    """Canonical slug absent from the committed classification table."""


def skill_mcp_predicated(slug_or_entity_id: str) -> bool:
    """Return whether the skill is MCP-predicated (fail-loud on missing row)."""
    key = canonical_table_key(slug_or_entity_id)
    try:
        return SKILL_MCP_PREDICATED[key]
    except KeyError as exc:
        raise SkillClassificationMissingError(
            f"canonical slug {key!r} absent from skill MCP classification "
            f"(template_version={TEMPLATE_VERSION})"
        ) from exc


def classification_bytes_for_digest() -> bytes:
    """Stable serialization for determinism / drift probes."""
    return json.dumps(
        SKILL_MCP_PREDICATED, sort_keys=True, separators=(",", ":")
    ).encode()

