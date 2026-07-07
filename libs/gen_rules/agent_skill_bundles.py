"""Emit `.cursor/skills/<slug>/SKILL.md` from agent-surface sources."""

from __future__ import annotations

import sys
from pathlib import Path

from claude_bundles.resolver import CURSOR_INDEXED_SLUGS

from .agent_guides import AGENT_GUIDES_RULE_SLUGS, normalize_rule_entry
from .check import diff_against
from .parser import parse_source
from .renderer import render_skill_bundle

# Overlap slugs cleared by STEP-0 reconcile (todo-lifecycle deferred — divergent body).
AGENT_SURFACE_SKILL_SLUGS: tuple[str, ...] = (
    "advisor-timing",
    "agent-identity-signoff",
    "handoff-pickup",
)

_GUIDES_KEYS = frozenset(AGENT_GUIDES_RULE_SLUGS)
_INDEXED = frozenset(CURSOR_INDEXED_SLUGS)
assert set(AGENT_SURFACE_SKILL_SLUGS) <= _GUIDES_KEYS, (
    f"AGENT_SURFACE_SKILL_SLUGS must be subset of AGENT_GUIDES_RULE_SLUGS; "
    f"extra={set(AGENT_SURFACE_SKILL_SLUGS) - _GUIDES_KEYS}"
)
assert set(AGENT_SURFACE_SKILL_SLUGS) <= _INDEXED, (
    f"AGENT_SURFACE_SKILL_SLUGS must be subset of CURSOR_INDEXED_SLUGS; "
    f"extra={set(AGENT_SURFACE_SKILL_SLUGS) - _INDEXED}"
)


def emit_agent_skill_bundles(
    root: Path,
    *,
    check: bool,
    dry_run: bool,
) -> int:
    """Generate or drift-check `.cursor/skills/<slug>/SKILL.md` bundle files."""
    fail = 0

    for slug in AGENT_SURFACE_SKILL_SLUGS:
        entry = normalize_rule_entry(AGENT_GUIDES_RULE_SLUGS[slug])
        source_rel = entry["source"]
        source_path = root / source_rel
        out_path = root / ".cursor" / "skills" / slug / "SKILL.md"

        if not source_path.exists():
            print(f"ERROR: missing source for {slug!r}: {source_path}", file=sys.stderr)
            fail = 1
            continue

        parsed = parse_source(source_path)
        if parsed.frontmatter_skill is None:
            print(
                f"ERROR: {source_path}: missing frontmatter:skill block",
                file=sys.stderr,
            )
            fail = 1
            continue

        try:
            rendered = render_skill_bundle(parsed, source_rel=source_rel, slug=slug)
        except ValueError as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            fail = 1
            continue

        if dry_run:
            print(f"--- {out_path} ---")
            print(rendered, end="")
            continue

        if check:
            current = out_path.read_text(encoding="utf-8") if out_path.is_file() else ""
            d = diff_against(
                current,
                rendered,
                label_expected=str(out_path),
                label_actual="<generated>",
            )
            if d:
                print(d, end="")
                print(
                    f"DRIFT: {out_path} out of sync with {source_rel}",
                    file=sys.stderr,
                )
                fail = 1
            continue

        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(rendered, encoding="utf-8")
        print(f"wrote {out_path}")

    return fail
