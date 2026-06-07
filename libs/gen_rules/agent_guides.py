"""Emit `docs/agent-guides/rules/*.md` from tagged agent-surface sources."""

from __future__ import annotations

import sys
from pathlib import Path

from .check import diff_against
from .parser import parse_source
from .renderer import _do_not_edit

# Explicit manifest — one output file per slug; grow incrementally (MVW conduct slice).
AGENT_GUIDES_RULE_SLUGS: dict[str, str] = {
    "provenance-discipline": "agent-surface/sources/provenance-discipline.md",
    "cortex-essentials": "agent-surface/sources/cortex-essentials.md",
    "advisor-timing": "agent-surface/sources/advisor-timing.md",
    "system-conduct": "agent-surface/sources/system-conduct.md",
    "agent-identity-signoff": "agent-surface/sources/agent-identity-signoff.md",
    "md-navigation": "agent-surface/sources/md-navigation.md",
    "capability-dispatch": "agent-surface/sources/capability-dispatch.md",
}


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

    for slug, source_rel in AGENT_GUIDES_RULE_SLUGS.items():
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
