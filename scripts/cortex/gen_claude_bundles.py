#!/usr/bin/env python3
"""Generate self-contained ``.claude/skills/`` bundles and hardlink into ``.cursor/skills/``."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent.parent
if str(_REPO / "libs") not in sys.path:
    sys.path.insert(0, str(_REPO / "libs"))

from claude_bundles.resolver import (  # noqa: E402
    CLAUDE_BUNDLE_SLUGS,
    render_bundle,
    resolve_sot,
)
from gen_rules.check import diff_against  # noqa: E402


def _workspace_root() -> Path:
    try:
        out = subprocess.check_output(
            ["git", "rev-parse", "--show-toplevel"],
            stderr=subprocess.DEVNULL,
            text=True,
        ).strip()
        return Path(out)
    except subprocess.CalledProcessError:
        return _REPO


def _bundle_paths(root: Path, slug: str) -> tuple[Path, Path]:
    claude = root / ".claude" / "skills" / slug / "SKILL.md"
    cursor = root / ".cursor" / "skills" / slug / "SKILL.md"
    return claude, cursor


def _load_rendered(slug: str, root: Path) -> tuple[Path, str, str]:
    sot_path, root_label = resolve_sot(slug, root)
    rendered = render_bundle(slug, sot_path.read_text(encoding="utf-8"))
    return sot_path, root_label, rendered


def run_dry_run(root: Path) -> int:
    print("DRY RUN — no writes will be issued")
    print()
    fail = 0
    for slug in CLAUDE_BUNDLE_SLUGS:
        try:
            sot_path, root_label, _ = _load_rendered(slug, root)
        except FileNotFoundError as exc:
            print(f"ERROR  {slug:32s}  {exc}", file=sys.stderr)
            fail = 1
            continue
        claude_path, cursor_path = _bundle_paths(root, slug)
        print(f"{slug:32s}  resolved={root_label}  ({sot_path})")
        print(f"  WRITE {claude_path.relative_to(root)}")
        if cursor_path.is_file():
            print(f"  SKIPPED (exists) {cursor_path.relative_to(root)}")
        else:
            print(f"  HARDLINK {cursor_path.relative_to(root)}")
    return fail


def run_check(root: Path) -> int:
    fail = 0
    for slug in CLAUDE_BUNDLE_SLUGS:
        try:
            _, _, rendered = _load_rendered(slug, root)
        except FileNotFoundError as exc:
            print(f"ERROR: {slug}: {exc}", file=sys.stderr)
            fail = 1
            continue
        claude_path, _ = _bundle_paths(root, slug)
        current = (
            claude_path.read_text(encoding="utf-8") if claude_path.is_file() else ""
        )
        diff = diff_against(
            current,
            rendered,
            label_expected=str(claude_path),
            label_actual="<generated>",
        )
        if diff:
            print(diff, end="")
            print(f"DRIFT: {claude_path} out of sync with SOT", file=sys.stderr)
            fail = 1
    if fail == 0:
        print("OK gen_claude_bundles --check")
    return fail


def run_generate(root: Path) -> int:
    fail = 0
    for slug in CLAUDE_BUNDLE_SLUGS:
        try:
            _, _, rendered = _load_rendered(slug, root)
        except FileNotFoundError as exc:
            print(f"ERROR: {slug}: {exc}", file=sys.stderr)
            fail = 1
            continue
        claude_path, cursor_path = _bundle_paths(root, slug)
        claude_path.parent.mkdir(parents=True, exist_ok=True)
        if (
            not claude_path.is_file()
            or claude_path.read_text(encoding="utf-8") != rendered
        ):
            claude_path.write_text(rendered, encoding="utf-8")
            print(f"wrote {claude_path}")
        if cursor_path.is_file():
            print(f"SKIPPED (exists) {cursor_path}")
            continue
        cursor_path.parent.mkdir(parents=True, exist_ok=True)
        os.link(claude_path, cursor_path)
        print(f"hardlinked {cursor_path}")
    return fail


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--root", type=Path, default=None)
    args = parser.parse_args(argv)
    if args.dry_run and args.check:
        print("ERROR: --dry-run and --check are mutually exclusive", file=sys.stderr)
        return 2
    root = (args.root or _workspace_root()).resolve()
    if args.dry_run:
        return run_dry_run(root)
    if args.check:
        return run_check(root)
    return run_generate(root)


if __name__ == "__main__":
    sys.exit(main())
