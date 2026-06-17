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
