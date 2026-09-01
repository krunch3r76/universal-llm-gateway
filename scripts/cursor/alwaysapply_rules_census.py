#!/usr/bin/env python3
"""Token census for Cursor ``alwaysApply: true`` rule files (a:31708 arc, agent-bus:9848).

Committed successor to the 2026-08-31 ephemeral heredoc that produced
``cortex://notes/system/threads/alwaysapply-rules-census-2026-08-31.md``. Scans
the three rule-body SoT trees (plugin, hub, parent pack), ranks always-applied
files by a chars/4 token estimate, and — with ``--check`` — fails when the
always-applied sum for a scan exceeds a budget (the G4 admission gate named in
the Fable G1 answer § Invariants, item 2: 8K target / 10K hard ceiling).

Cursor's injection key is only the ``alwaysApply`` frontmatter flag; neither
``apply_tier`` nor ``trigger_match_terms`` demotes a file, so this script does
not weight by either.

Run:
    ~/.venvs/universal/bin/python scripts/cursor/alwaysapply_rules_census.py
    ~/.venvs/universal/bin/python scripts/cursor/alwaysapply_rules_census.py --check 10000
    ~/.venvs/universal/bin/python scripts/cursor/alwaysapply_rules_census.py --quiet --check 10000
    ~/.venvs/universal/bin/python scripts/cursor/alwaysapply_rules_census.py --root /path/to/rules --tree adhoc
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]

_DEFAULT_ROOTS: tuple[tuple[str, Path], ...] = (
    ("plugin", _REPO / "cursor-plugins" / "ulg-ecosystem" / "rules"),
    ("hub", _REPO / ".cursor" / "rules"),
    ("parent", _REPO.parent / ".cursor" / "rules"),
)

_FRONTMATTER = re.compile(r"^---\n(.*?)\n---\n?", re.DOTALL)
_ALWAYS_APPLY = re.compile(r"^alwaysApply:\s*(true|false)\s*$", re.MULTILINE | re.IGNORECASE)
_APPLY_TIER = re.compile(r"^apply_tier:\s*(\S+)\s*$", re.MULTILINE)


@dataclass(frozen=True)
class RuleFile:
    """One scanned ``.mdc`` rule with its always-apply status and token estimate."""

    tree: str
    path: Path
    always_apply: bool
    apply_tier: str | None
    tokens: int
    lines: int

    @property
    def name(self) -> str:
        return self.path.name


def _parse_frontmatter(text: str) -> tuple[bool, str | None]:
    """Return ``(always_apply, apply_tier)`` from a rule file's YAML frontmatter.

    Regex, not a YAML parser — these rule bodies are hand-authored with a
    known-flat frontmatter shape, and pulling in a YAML dependency for two
    scalar fields would be the wrong tool for the job.
    """
    match = _FRONTMATTER.match(text)
    if not match:
        return False, None
    block = match.group(1)
    always_match = _ALWAYS_APPLY.search(block)
    always_apply = bool(always_match) and always_match.group(1).lower() == "true"
    tier_match = _APPLY_TIER.search(block)
    apply_tier = tier_match.group(1) if tier_match else None
    return always_apply, apply_tier


def _scan_tree(tree: str, root: Path) -> list[RuleFile]:
    if not root.is_dir():
        return []
    out: list[RuleFile] = []
    for path in sorted(root.glob("*.mdc")):
        text = path.read_text(encoding="utf-8")
        always_apply, apply_tier = _parse_frontmatter(text)
        out.append(
            RuleFile(
                tree=tree,
                path=path,
                always_apply=always_apply,
                apply_tier=apply_tier,
                tokens=len(text) // 4,
                lines=text.count("\n") + 1,
            )
        )
    return out


def scan_roots(roots: tuple[tuple[str, Path], ...]) -> list[RuleFile]:
    files: list[RuleFile] = []
    for tree, root in roots:
        files.extend(_scan_tree(tree, root))
    return files


def render_report(files: list[RuleFile]) -> str:
    always = sorted((f for f in files if f.always_apply), key=lambda f: f.tokens, reverse=True)
    total_tokens = sum(f.tokens for f in files)
    always_tokens = sum(f.tokens for f in always)

    lines = [
        "# AlwaysApply rule census",
        "",
        "Measured from workspace SoT (not Cursor's in-session wrapping).",
        f"- files total: {len(files)}",
        f"- alwaysApply:true: {len(always)}",
        f"- alwaysApply tokens (chars/4): **{always_tokens}**",
        f"- all .mdc tokens (chars/4): {total_tokens}",
        "",
        "## alwaysApply:true ranked by tokens",
        "",
        "| tok≈ | lines | tree | apply_tier | file |",
        "|---:|---:|---|---|---|",
    ]
    for f in always:
        lines.append(f"| {f.tokens} | {f.lines} | {f.tree} | {f.apply_tier or '—'} | `{f.name}` |")

    lines.append("")
    lines.append("## alwaysApply:true by tree")
    lines.append("")
    for tree in sorted({f.tree for f in always}):
        tree_files = [f for f in always if f.tree == tree]
        lines.append(f"- `{tree}`: n={len(tree_files)} tok≈{sum(f.tokens for f in tree_files)}")

    return "\n".join(lines) + "\n"


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root",
        action="append",
        default=None,
        metavar="PATH",
        help="Additional rule-body directory to scan (repeatable). "
        "Replaces the default three trees when --tree is also given.",
    )
    parser.add_argument(
        "--tree",
        action="append",
        default=None,
        metavar="NAME",
        help="Tree label paired positionally with --root.",
    )
    parser.add_argument(
        "--check",
        type=int,
        default=None,
        metavar="BUDGET",
        help="Exit 1 if the alwaysApply:true token sum exceeds BUDGET.",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Skip the markdown report; --check PASS/FAIL still go to stderr.",
    )
    args = parser.parse_args(argv)
    if bool(args.root) != bool(args.tree):
        parser.error("--root and --tree must be supplied together, same count")
    if args.root and len(args.root) != len(args.tree):
        parser.error("--root and --tree counts must match")
    return args


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(sys.argv[1:] if argv is None else argv)
    roots = (
        tuple(zip(args.tree, (Path(r) for r in args.root)))
        if args.root
        else _DEFAULT_ROOTS
    )
    files = scan_roots(roots)
    if not args.quiet:
        print(render_report(files))

    if args.check is not None:
        always_tokens = sum(f.tokens for f in files if f.always_apply)
        if always_tokens > args.check:
            print(
                f"FAIL: alwaysApply tokens {always_tokens} exceed budget {args.check}",
                file=sys.stderr,
            )
            return 1
        print(f"PASS: alwaysApply tokens {always_tokens} <= budget {args.check}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
