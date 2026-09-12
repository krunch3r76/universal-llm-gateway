#!/usr/bin/env python3
"""Token census for Cursor ``alwaysApply: true`` rule files (a:31708 arc, agent-bus:9848).

Meter (pinned): chars/4 on decoded UTF-8 (``len(text) // 4``). Byte-based
counts of these symbol-dense files run ~2% higher (e.g. dispatch-kernel
1343 vs 1315). Do not publish a second meter.

Three planes, three verdicts:

* **Resident** (plugin + hub + parent): G0 8K raw target / 10K install hard;
  plan gate **9000** via ``--check 9000``.
* **Seat-effective** (cursor-sdk dispatch HOME after overlay prune+graft):
  same **9000** hard when ``--check 9000`` is set. Imports prune lists from
  ``cursor_seat_overlay`` — never copy path literals.
* **Seats** (``cursor-plugins/ulg-ecosystem-seats/cursor-sdk/rules``): own
  budget (3K target / 4K hard). Not a member of the resident sum.

``--check`` (no value) runs resident + seat-effective + seats + per-file 1100
ceiling on the resident plane. ``_PER_FILE_EXEMPTIONS`` must stay empty
(phase-2 bind). ``--quiet`` never hides WARN/FAIL.

Cursor's injection key is only the ``alwaysApply`` frontmatter flag; neither
``apply_tier`` nor ``trigger_match_terms`` demotes a file unless
``alwaysApply: false``.

Run:
    ~/.venvs/universal/bin/python scripts/cursor/alwaysapply_rules_census.py
    ~/.venvs/universal/bin/python scripts/cursor/alwaysapply_rules_census.py --check 9000
    ~/.venvs/universal/bin/python scripts/cursor/alwaysapply_rules_census.py --quiet --check 9000
    ~/.venvs/universal/bin/python scripts/cursor/alwaysapply_rules_census.py --root /path/to/rules --tree adhoc
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]

if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from services.git_integration_worker.cursor_seat_overlay import (  # noqa: E402
    LEAD_ONLY_PLUGIN_PATHS,
    PRUNED_PLUGIN_PATHS,
)

_DEFAULT_ROOTS: tuple[tuple[str, Path], ...] = (
    ("plugin", _REPO / "cursor-plugins" / "ulg-ecosystem" / "rules"),
    ("hub", _REPO / ".cursor" / "rules"),
    ("parent", _REPO.parent / ".cursor" / "rules"),
)
_SEATS_ROOT = _REPO / "cursor-plugins" / "ulg-ecosystem-seats" / "cursor-sdk" / "rules"

_RESIDENT_TARGET = 8000
_RESIDENT_HARD = 10000
_SEATS_TARGET = 3000
_SEATS_HARD = 4000
_SEAT_EFFECTIVE_TARGET = 8000
_SEAT_EFFECTIVE_HARD = 9000
_PER_FILE_CEILING = 1100
_PER_FILE_EXEMPTIONS: dict[str, str] = {}

_PLUGIN_RULES_ROOT = Path("rules")
_REMOVED_PLUGIN_RULES: frozenset[Path] = frozenset(
    p
    for p in (*PRUNED_PLUGIN_PATHS, *LEAD_ONLY_PLUGIN_PATHS)
    if p.parts[:1] == (_PLUGIN_RULES_ROOT.name,)
)

_FRONTMATTER = re.compile(r"^---\n(.*?)\n---\n?", re.DOTALL)
_ALWAYS_APPLY = re.compile(
    r"^alwaysApply:\s*(true|false)\s*$", re.MULTILINE | re.IGNORECASE
)
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


def estimate_tokens(text: str) -> int:
    """chars/4 on decoded UTF-8. Byte-based counts run ~2% higher on these files."""
    return len(text) // 4


def _parse_frontmatter(text: str) -> tuple[bool, str | None]:
    """``(always_apply, apply_tier)`` from flat YAML frontmatter — regex, not a parser."""
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
                tokens=estimate_tokens(text),
                lines=text.count("\n") + 1,
            )
        )
    return out


def scan_roots(roots: tuple[tuple[str, Path], ...]) -> list[RuleFile]:
    files: list[RuleFile] = []
    for tree, root in roots:
        files.extend(_scan_tree(tree, root))
    return files


def _always(files: list[RuleFile]) -> list[RuleFile]:
    return sorted(
        (f for f in files if f.always_apply), key=lambda f: f.tokens, reverse=True
    )


def _always_tokens(files: list[RuleFile]) -> int:
    return sum(f.tokens for f in files if f.always_apply)


def seat_effective_tokens(resident: list[RuleFile], seats: list[RuleFile]) -> int:
    """Dispatch HOME: hub + parent + plugin minus pruned/lead-only rules + seat graft."""
    total = 0
    for f in resident:
        if not f.always_apply:
            continue
        if f.tree == "plugin":
            relpath = _PLUGIN_RULES_ROOT / f.name
            if relpath in _REMOVED_PLUGIN_RULES:
                continue
        total += f.tokens
    total += _always_tokens(seats)
    return total


def render_report(files: list[RuleFile], seats: list[RuleFile] | None = None) -> str:
    always = _always(files)
    lines = [
        "# AlwaysApply rule census",
        "",
        "Meter: chars/4 on decoded UTF-8 (byte-based counts run ~2% higher on these files).",
        "Measured from workspace SoT (not Cursor's in-session wrapping).",
        "",
        "## Resident plane (plugin / hub / parent)",
        "",
        f"- files total: {len(files)}",
        f"- alwaysApply:true: {len(always)}",
        f"- alwaysApply tokens (chars/4): **{_always_tokens(files)}**",
        f"- all .mdc tokens (chars/4): {sum(f.tokens for f in files)}",
        "",
        "## alwaysApply:true ranked by tokens",
        "",
        "| tok≈ | lines | tree | apply_tier | file |",
        "|---:|---:|---|---|---|",
    ]
    for f in always:
        lines.append(
            f"| {f.tokens} | {f.lines} | {f.tree} | {f.apply_tier or '—'} | `{f.name}` |"
        )
    lines.append("")
    lines.append("## alwaysApply:true by tree")
    lines.append("")
    for tree in sorted({f.tree for f in always}):
        tree_files = [f for f in always if f.tree == tree]
        lines.append(
            f"- `{tree}`: n={len(tree_files)} tok≈{sum(f.tokens for f in tree_files)}"
        )
    if seats is not None:
        s_always = _always(seats)
        eff = seat_effective_tokens(files, seats)
        lines.extend(
            [
                "",
                "## Seat-effective plane (cursor-sdk dispatch HOME)",
                "",
                f"- pruned plugin rules (from overlay): n={len(_REMOVED_PLUGIN_RULES)}",
                f"- seat-effective tokens (chars/4): **{eff}**",
                "",
                "## Seats plane (cursor-sdk overlay graft)",
                "",
                f"- files total: {len(seats)}",
                f"- alwaysApply:true: {len(s_always)}",
                f"- seats tokens (chars/4): **{_always_tokens(seats)}**",
                "",
                "| tok≈ | lines | tree | apply_tier | file |",
                "|---:|---:|---|---|---|",
            ]
        )
        for f in s_always:
            lines.append(
                f"| {f.tokens} | {f.lines} | {f.tree} | {f.apply_tier or '—'} | `{f.name}` |"
            )
    return "\n".join(lines) + "\n"


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--root",
        action="append",
        default=None,
        metavar="PATH",
        help="Scan directory; with --tree replaces resident defaults (no seats plane).",
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
        nargs="?",
        const=-1,
        type=int,
        default=None,
        metavar="HARD",
        help="Admission gate. Default: resident+seat-effective+seats+per-file. "
        "HARD overrides resident/seat-effective hard (e.g. 9000).",
    )
    parser.add_argument(
        "--target",
        type=int,
        default=None,
        metavar="N",
        help=f"Resident warn tier (default {_RESIDENT_TARGET}).",
    )
    parser.add_argument(
        "--seats-target",
        type=int,
        default=None,
        metavar="N",
        help=f"Seats-plane warn tier (default {_SEATS_TARGET}).",
    )
    parser.add_argument(
        "--seats-hard",
        type=int,
        default=None,
        metavar="N",
        help=f"Seats-plane hard ceiling (default {_SEATS_HARD}).",
    )
    parser.add_argument(
        "--per-file",
        type=int,
        default=None,
        metavar="N",
        help=f"Resident per-file ceiling (default {_PER_FILE_CEILING}).",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Skip markdown report; PASS/WARN/FAIL still go to stderr.",
    )
    args = parser.parse_args(argv)
    if bool(args.root) != bool(args.tree):
        parser.error("--root and --tree must be supplied together, same count")
    if args.root and len(args.root) != len(args.tree):
        parser.error("--root and --tree counts must match")
    return args


def _emit_plane(name: str, tokens: int, target: int, hard: int) -> str:
    if tokens > hard:
        status, detail = "FAIL", f"exceed hard {hard}"
    elif tokens > target:
        status, detail = "WARN", f"> target {target} (hard {hard})"
    else:
        status, detail = "PASS", f"<= target {target} (hard {hard})"
    print(f"{status}: {name} alwaysApply tokens {tokens} {detail}", file=sys.stderr)
    return status


def _emit_per_file(files: list[RuleFile], ceiling: int) -> str:
    """Resident-plane per-file check. No exemptions — breach is FAIL."""
    worst = "PASS"
    over = 0
    for f in sorted((x for x in files if x.always_apply), key=lambda x: x.name):
        if f.tokens <= ceiling:
            continue
        over += 1
        reason = _PER_FILE_EXEMPTIONS.get(f.name)
        if reason:
            print(
                f"WARN: per-file {f.name} {f.tokens} > {ceiling} (exempt: {reason})",
                file=sys.stderr,
            )
            if worst == "PASS":
                worst = "WARN"
            continue
        print(f"FAIL: per-file {f.name} {f.tokens} > {ceiling}", file=sys.stderr)
        worst = "FAIL"
    if over == 0:
        print(
            f"PASS: per-file all resident alwaysApply files <= {ceiling}",
            file=sys.stderr,
        )
    return worst


def _legacy_sum_check(files: list[RuleFile], hard: int) -> int:
    tokens = _always_tokens(files)
    if tokens > hard:
        print(
            f"FAIL: alwaysApply tokens {tokens} exceed budget {hard}", file=sys.stderr
        )
        return 1
    print(f"PASS: alwaysApply tokens {tokens} <= budget {hard}", file=sys.stderr)
    return 0


def _run_policy(
    resident: list[RuleFile],
    seats: list[RuleFile],
    seats_root: Path,
    args: argparse.Namespace,
) -> int:
    if not seats_root.is_dir():
        print(f"FAIL: seats tree missing: {seats_root}", file=sys.stderr)
        return 1
    if args.check == -1:
        hard = _RESIDENT_HARD
        seat_hard = _SEAT_EFFECTIVE_HARD
    else:
        hard = args.check
        seat_hard = args.check
    target = _RESIDENT_TARGET if args.target is None else args.target
    seat_target = (
        _SEAT_EFFECTIVE_TARGET if args.target is None else min(args.target, hard)
    )
    s_target = _SEATS_TARGET if args.seats_target is None else args.seats_target
    s_hard = _SEATS_HARD if args.seats_hard is None else args.seats_hard
    ceiling = _PER_FILE_CEILING if args.per_file is None else args.per_file
    eff = seat_effective_tokens(resident, seats)
    statuses = (
        _emit_plane("resident", _always_tokens(resident), target, hard),
        _emit_plane("seat-effective", eff, seat_target, seat_hard),
        _emit_plane("seats", _always_tokens(seats), s_target, s_hard),
        _emit_per_file(resident, ceiling),
    )
    return 1 if "FAIL" in statuses else 0


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(sys.argv[1:] if argv is None else argv)
    if args.root:
        roots = tuple(zip(args.tree, (Path(r) for r in args.root)))
        files = scan_roots(roots)
        if not args.quiet:
            print(render_report(files))
        if args.check is None:
            return 0
        hard = _RESIDENT_HARD if args.check == -1 else args.check
        return _legacy_sum_check(files, hard)

    resident = scan_roots(_DEFAULT_ROOTS)
    seats = _scan_tree("seats", _SEATS_ROOT)
    if not args.quiet:
        print(render_report(resident, seats=seats))
    if args.check is None:
        return 0
    return _run_policy(resident, seats, _SEATS_ROOT, args)


if __name__ == "__main__":
    raise SystemExit(main())
