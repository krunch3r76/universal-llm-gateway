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


def patch_skill(skill_path: Path) -> None:
    text = skill_path.read_text(encoding="utf-8")
    patched = embed_workflow_registry_block(text)
    if patched != text:
        skill_path.write_text(patched, encoding="utf-8")
    if not verify_workflow_registry_drift(skill_path):
        msg = f"workflow-registry drift check failed after patch: {skill_path}"
        raise SystemExit(msg)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
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
        patch_skill(skill_path)
        print(f"patched workflow-registry block: {skill_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
