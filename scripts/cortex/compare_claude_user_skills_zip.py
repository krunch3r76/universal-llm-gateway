#!/usr/bin/env python3
"""Compare a claude.ai container skills zip (skills/user/) to catalog + upload bytes.

Only ``user/`` is the fleet Customize library. Public/examples are Anthropic
stock — subtracted from extras. Recon applies the 1:1 plan (uninstall extras,
upload missing, replace stale) via ``reconcile_claude_user_skills.py``.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent.parent
if str(_REPO / "libs") not in sys.path:
    sys.path.insert(0, str(_REPO / "libs"))

from claude_bundles.container_skills_zip import (  # noqa: E402
    compare_user_zip,
    zip_tree_inventory,
)
from claude_bundles.customize_upload_bytes import build_staged_bytes_map  # noqa: E402
from claude_bundles.resolver import claude_ai_target_slugs  # noqa: E402


def print_inventory(zip_path: Path) -> None:
    inv = zip_tree_inventory(zip_path)
    print(
        f"public={len(inv['public'])} examples={len(inv['examples'])} "
        f"user={len(inv['user'])}"
    )
    for tree, slugs in inv.items():
        print(f"{tree}:")
        for slug in slugs:
            print(f"  {slug}")


def print_compare(zip_path: Path, repo: Path, *, as_json: bool) -> int:
    plan = compare_user_zip(
        zip_path,
        catalog=set(claude_ai_target_slugs()),
        staged_bytes=build_staged_bytes_map(repo),
    )
    if as_json:
        print(json.dumps(plan.as_dict(), indent=2))
        return 0 if plan.mirrored() else 1
    print(
        f"user={len(plan.user)} catalog={len(plan.catalog)} "
        f"match={len(plan.match)} stale={len(plan.stale)}"
    )
    if plan.extra:
        print(f"extra_in_user ({len(plan.extra)}):")
        for slug in plan.extra:
            print(f"  {slug}")
    if plan.missing:
        print(f"missing_from_user ({len(plan.missing)}):")
        for slug in plan.missing:
            print(f"  {slug}")
    if plan.stale:
        print(f"stale_content ({len(plan.stale)}):")
        for slug, zlen, llen in plan.stale_sizes:
            print(f"  {slug}: zip={zlen} staged={llen}")
    if plan.mirrored():
        print("OK user zip is a 1:1 catalog mirror (names + upload SKILL.md hashes)")
        return 0
    return 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("zip_path", type=Path)
    parser.add_argument("--root", type=Path, default=_REPO)
    parser.add_argument(
        "--inventory",
        action="store_true",
        help="Print public/examples/user slugs (stock + user). Does not compare.",
    )
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    if args.inventory:
        print_inventory(args.zip_path)
        return 0
    return print_compare(args.zip_path, args.root, as_json=args.json)


if __name__ == "__main__":
    raise SystemExit(main())
