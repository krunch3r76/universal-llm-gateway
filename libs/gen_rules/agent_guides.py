"""Emit `docs/agent-guides/rules/*.md` from tagged agent-surface sources."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import TypedDict

from agent_seat.profiles import known_seats

from .check import diff_against
from .parser import parse_source
from .renderer import _do_not_edit

UNIVERSAL_AGENTS = ["*"]


class RuleManifestEntry(TypedDict, total=False):
    source: str
    applicable_agents: list[str]
    capabilities_required: list[str]
    delivery_priority: int


# Explicit manifest — one output file per slug; grow incrementally (MVW conduct slice).
AGENT_GUIDES_RULE_SLUGS: dict[str, RuleManifestEntry | str] = {
    "provenance-discipline": {
        "source": "agent-surface/sources/provenance-discipline.md",
        "applicable_agents": ["*"],
    },
    "cortex-essentials": {
        "source": "agent-surface/sources/cortex-essentials.md",
        "applicable_agents": ["*"],
    },
    "advisor-timing": {
        "source": "agent-surface/sources/advisor-timing.md",
        "applicable_agents": ["*"],
    },
    "system-conduct": {
        "source": "agent-surface/sources/system-conduct.md",
        "applicable_agents": ["*"],
    },
    "agent-identity-signoff": {
        "source": "agent-surface/sources/agent-identity-signoff.md",
        "applicable_agents": ["*"],
    },
    "md-navigation": {
        "source": "agent-surface/sources/md-navigation.md",
        "applicable_agents": ["*"],
    },
    "capability-dispatch": {
        "source": "agent-surface/sources/capability-dispatch.md",
        "applicable_agents": ["*"],
    },
    "handoff-pickup": {
        "source": "agent-surface/sources/handoff-pickup.md",
        "applicable_agents": ["*"],
    },
    "plan-slug-coherence": {
        "source": "agent-surface/sources/plan-slug-coherence.md",
        "applicable_agents": ["*"],
    },
    # Wave 5b.2 unit (c) additions — see notes/system/recon/rules-skills-mcp-reframe/
    # unitc-applicability-curation.md for the locked disposition of each slug.
    "modularization": {
        "source": "agent-surface/sources/modularization.md",
        "applicable_agents": ["*"],
    },
    "python312": {
        "source": "agent-surface/sources/python312.md",
        "applicable_agents": ["*"],
    },
    "javascript-modern": {
        "source": "agent-surface/sources/javascript-modern.md",
        "applicable_agents": ["*"],
    },
    "php-modern": {
        "source": "agent-surface/sources/php-modern.md",
        "applicable_agents": ["*"],
    },
    "php-api": {
        "source": "agent-surface/sources/php-api.md",
        "applicable_agents": ["*"],
    },
    "pwa-patterns": {
        "source": "agent-surface/sources/pwa-patterns.md",
        "applicable_agents": ["*"],
    },
    "testing-discipline": {
        "source": "agent-surface/sources/testing-discipline.md",
        "applicable_agents": ["*"],
    },
    "doc-patterns": {
        "source": "agent-surface/sources/doc-patterns.md",
        "applicable_agents": ["*"],
    },
    "session-transcript-fidelity": {
        "source": "agent-surface/sources/session-transcript-fidelity.md",
        "applicable_agents": ["*"],
    },
    "phase-vocabulary": {
        "source": "agent-surface/sources/phase-vocabulary.md",
        "applicable_agents": ["*"],
    },
    "lessons": {
        "source": "agent-surface/sources/lessons.md",
        "applicable_agents": ["*"],
    },
    "phase-policy": {
        "source": "agent-surface/sources/phase-policy.md",
        "applicable_agents": ["*"],
    },
    "todo-lifecycle": {
        "source": "agent-surface/sources/todo-lifecycle.md",
        "applicable_agents": ["*"],
    },
    "deployment-topology": {
        "source": "agent-surface/sources/deployment-topology.md",
        "applicable_agents": ["*"],
    },
    "docs-write-guard": {
        "source": "agent-surface/sources/docs-write-guard.md",
        "applicable_agents": ["*"],
    },
    "cortex-workbench": {
        "source": "agent-surface/sources/cortex-workbench.md",
        "applicable_agents": ["*"],
    },
    "request-routing": {
        "source": "agent-surface/sources/request-routing.md",
        "applicable_agents": ["*"],
    },
    "federation-architecture": {
        "source": "agent-surface/sources/federation-architecture.md",
        "applicable_agents": ["*"],
    },
    "pipeline-development": {
        "source": "agent-surface/sources/pipeline-development.md",
        "applicable_agents": ["*"],
    },
    "rag-architecture": {
        "source": "agent-surface/sources/rag-architecture.md",
        "applicable_agents": ["*"],
    },
    "stargate-live-state": {
        "source": "agent-surface/sources/stargate-live-state.md",
        "applicable_agents": ["*"],
    },
    "cortex-feature-registry": {
        "source": "agent-surface/sources/cortex-feature-registry.md",
        "applicable_agents": ["*"],
    },
    "model-catalog-ids": {
        "source": "agent-surface/sources/model-catalog-ids.md",
        "applicable_agents": ["*"],
    },
    "cloud-model-routing": {
        "source": "agent-surface/sources/cloud-model-routing.md",
        "applicable_agents": ["*"],
    },
    "event-system-reference": {
        "source": "agent-surface/sources/event-system-reference.md",
        "applicable_agents": ["*"],
    },
    "pipeline-consensus-patterns": {
        "source": "agent-surface/sources/pipeline-consensus-patterns.md",
        "applicable_agents": ["*"],
    },
    "pipeline-viewer-conventions": {
        "source": "agent-surface/sources/pipeline-viewer-conventions.md",
        "applicable_agents": ["*"],
    },
    "pipeline-testing": {
        "source": "agent-surface/sources/pipeline-testing.md",
        "applicable_agents": ["*"],
    },
    "frontier-model-context-policy": {
        "source": "agent-surface/sources/frontier-model-context-policy.md",
        "applicable_agents": ["*"],
    },
    "mcp-selector-naming": {
        "source": "agent-surface/sources/mcp-selector-naming.md",
        "applicable_agents": ["*"],
    },
    "mcp-tool-param-types": {
        "source": "agent-surface/sources/mcp-tool-param-types.md",
        "applicable_agents": ["*"],
    },
    "consensus-metrics-debugging": {
        "source": "agent-surface/sources/consensus-metrics-debugging.md",
        "applicable_agents": ["*"],
    },
    "yaml-field-naming": {
        "source": "agent-surface/sources/yaml-field-naming.md",
        "applicable_agents": ["*"],
    },
    "json-schema-gguf": {
        "source": "agent-surface/sources/json-schema-gguf.md",
        "applicable_agents": ["*"],
    },
    "vision-extensions": {
        "source": "agent-surface/sources/vision-extensions.md",
        "applicable_agents": ["*"],
    },
    "engineering-conduct": {
        "source": "agent-surface/sources/engineering-conduct.md",
        "applicable_agents": ["*"],
    },
}


def normalize_rule_entry(entry: RuleManifestEntry | str) -> RuleManifestEntry:
    """Return a manifest entry with defaulted applicability fields."""
    if isinstance(entry, str):
        return {
            "source": entry,
            "applicable_agents": list(UNIVERSAL_AGENTS),
            "capabilities_required": [],
            "delivery_priority": 100,
        }
    return {
        "source": entry["source"],
        "applicable_agents": list(entry.get("applicable_agents", UNIVERSAL_AGENTS)),
        "capabilities_required": list(entry.get("capabilities_required", [])),
        "delivery_priority": int(entry.get("delivery_priority", 100)),
    }


def validate_rule_manifest_slugs(
    manifest: dict[str, RuleManifestEntry | str] | None = None,
) -> None:
    """Fail loud if any applicable_agents slug is outside the seat registry."""
    allowed = known_seats() | {"*"}
    source = manifest if manifest is not None else AGENT_GUIDES_RULE_SLUGS
    bad: list[str] = []
    for slug, raw in source.items():
        entry = normalize_rule_entry(raw)
        unknown = sorted(set(entry["applicable_agents"]) - allowed)
        if unknown:
            bad.append(f"{slug}={unknown}")
    if bad:
        raise SystemExit(
            f"agents.yaml-unknown slugs in rule manifest — {bad}; "
            f"allowed={sorted(allowed)}"
        )


validate_rule_manifest_slugs()


def render_agent_guides_rule(parsed, source_rel: str) -> str:
    """Plain markdown for web-claude / connector read surface (target:* blocks)."""
    body = "".join(b.content for b in parsed.blocks if b.target == "*")
    out = _do_not_edit(source_rel) + "\n" + body
    if not out.endswith("\n"):
        out += "\n"
    return out


def emit_agent_guides_rules(
    root: Path,
    *,
    check: bool,
    dry_run: bool,
) -> int:
    """Generate or drift-check docs/agent-guides/rules/*.md."""
    fail = 0
    out_dir = root / "docs/agent-guides/rules"

    for slug, raw_entry in AGENT_GUIDES_RULE_SLUGS.items():
        entry = normalize_rule_entry(raw_entry)
        source_rel = entry["source"]
        source_path = root / source_rel
        out_path = out_dir / f"{slug}.md"
        if not source_path.exists():
            print(f"ERROR: missing source for {slug!r}: {source_path}", file=sys.stderr)
            fail = 1
            continue

        parsed = parse_source(source_path)
        rendered = render_agent_guides_rule(parsed, source_rel=source_rel)

        if dry_run:
            print(f"--- {out_path} ---")
            print(rendered, end="")
            continue

        if check:
            current = out_path.read_text() if out_path.exists() else ""
            d = diff_against(
                current,
                rendered,
                label_expected=str(out_path),
                label_actual="<generated>",
            )
            if d:
                print(d, end="")
                print(
                    f"DRIFT: {out_path} out of sync with {source_rel}", file=sys.stderr
                )
                fail = 1
            continue

        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(rendered)
        print(f"wrote {out_path}")

    return fail
