#!/usr/bin/env python3
"""Patch generated policy blocks into ulg-ecosystem skill bodies at install time."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from implement_admission.conductor_width_seat import (
    WIDTH_SEAT_MARKER_START,
    embed_width_seat_block,
)
from implement_admission.workflow_registry import (
    embed_workflow_registry_block,
    verify_workflow_registry_drift,
)

_REPO_ROOT = Path(__file__).resolve().parents[2]
_PLUGIN_ROOT = _REPO_ROOT / "cursor-plugins" / "ulg-ecosystem"


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


def _width_seat_files() -> list[Path]:
    out: list[Path] = []
    for pattern in ("*.md", "*.mdc"):
        for path in sorted(_PLUGIN_ROOT.rglob(pattern)):
            if WIDTH_SEAT_MARKER_START in path.read_text(encoding="utf-8"):
                out.append(path)
    return out


def check_width_seat(path: Path) -> bool:
    text = path.read_text(encoding="utf-8")
    return embed_width_seat_block(text) == text


def patch_width_seat(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    patched = embed_width_seat_block(text)
    if patched != text:
        path.write_text(patched, encoding="utf-8")
        print(f"patched width-seat block: {path}")


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
        default=None,
        help="Path to consult-routing SKILL.md (repeatable)",
    )
    parser.add_argument(
        "--width-seat",
        action="store_true",
        help="Patch or check width-seat blocks under cursor-plugins/ulg-ecosystem",
    )
    args = parser.parse_args(argv)
    if not args.skill and not args.width_seat:
        parser.error("at least one of --skill or --width-seat is required")
    exit_code = 0
    if args.skill:
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
    if args.width_seat:
        stale: list[Path] = []
        for path in _width_seat_files():
            if args.check:
                if not check_width_seat(path):
                    stale.append(path)
                    print(f"ERROR: width-seat block drift-stale: {path}", file=sys.stderr)
                else:
                    print(f"width-seat block ok: {path}")
            else:
                patch_width_seat(path)
        if args.check and stale:
            exit_code = 1
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
