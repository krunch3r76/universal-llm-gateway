#!/usr/bin/env python3
"""Patch generated policy blocks into ulg-ecosystem skill bodies at install time."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from implement_admission.workflow_registry import (
    embed_workflow_registry_block,
    verify_workflow_registry_drift,
)


def check_skill(skill_path: Path) -> bool:
    """Return True when *skill_path* embed matches the machine-readable registry."""
    return verify_workflow_registry_drift(skill_path)


def patch_skill(skill_path: Path) -> None:
    text = skill_path.read_text(encoding="utf-8")
    patched = embed_workflow_registry_block(text)
    if patched != text:
        skill_path.write_text(patched, encoding="utf-8")
    if not check_skill(skill_path):
        msg = f"workflow-registry drift check failed after patch: {skill_path}"
        raise SystemExit(msg)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="Verify drift only — do not patch (exit 1 when stale)",
    )
    parser.add_argument(
        "--skill",
        action="append",
        type=Path,
        required=True,
        help="Path to consult-routing SKILL.md (repeatable)",
    )
    args = parser.parse_args(argv)
    for skill_path in args.skill:
        if not skill_path.is_file():
            print(f"ERROR: skill file missing: {skill_path}", file=sys.stderr)
            return 1
        if args.check:
            if not check_skill(skill_path):
                print(
                    f"ERROR: workflow-registry block drift-stale: {skill_path}",
                    file=sys.stderr,
                )
                return 1
            print(f"workflow-registry block ok: {skill_path}")
            continue
        patch_skill(skill_path)
        print(f"patched workflow-registry block: {skill_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
