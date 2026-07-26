#!/usr/bin/env -S python3.12
"""Drift gate: bound-invariant falsifier must be reachable on the agent surface.

Verifies agent-surface/sources/mcp-tool-awareness.md (SOT) and the generated
cursor-plugins/ulg-ecosystem/rules/mcp-tool-awareness_ulg.mdc both carry the
G10 falsifier tier entry. Exit 0 if present, 1 if dropped.

Self-test: --expect-missing proves the gate fires (A3 regression proof).
Run via scripts/agent-surface-check.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SOURCE_MD = REPO_ROOT / "agent-surface/sources/mcp-tool-awareness.md"
SURFACE_MDC = (
    REPO_ROOT / "cursor-plugins/ulg-ecosystem/rules/mcp-tool-awareness_ulg.mdc"
)

# Stable markers — dropping any one breaks agent reachability for the G10 tier.
REQUIRED_MARKERS = (
    "bound-invariant-falsifier",
    "invariant-falsifier-check",
    "test_falsifier_",
    "Bound-Invariant Falsifier (G10)",
)


def _missing_markers(text: str) -> list[str]:
    return [m for m in REQUIRED_MARKERS if m not in text]


def check(*, surface_path: Path = SURFACE_MDC, source_path: Path = SOURCE_MD) -> list[str]:
    errors: list[str] = []
    for label, path in (("source", source_path), ("generated surface", surface_path)):
        if not path.is_file():
            errors.append(f"{label} missing: {path}")
            continue
        missing = _missing_markers(path.read_text(encoding="utf-8"))
        if missing:
            errors.append(
                f"{label} {path}: dropped G10 falsifier markers {missing!r}"
            )
    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--expect-missing",
        action="store_true",
        help="Self-test: assert the gate fails when the surface entry is absent",
    )
    args = parser.parse_args(argv)

    if args.expect_missing:
        blank = REPO_ROOT / "scripts/fixtures/falsifier-surface-blank.mdc"
        blank.parent.mkdir(parents=True, exist_ok=True)
        blank.write_text("# blank\n", encoding="utf-8")
        errors = check(surface_path=blank)
        blank.unlink(missing_ok=True)
        if not errors:
            print(
                "ERROR: expect-missing self-test did not detect dropped falsifier entry",
                file=sys.stderr,
            )
            return 2
        print("OK expect-missing: gate fired on absent falsifier surface entry")
        return 0

    errors = check()
    if errors:
        for line in errors:
            print(line, file=sys.stderr)
        return 1

    print(
        "OK check-falsifier-surface-reach: "
        "bound-invariant-falsifier tier in mcp-tool-awareness_ulg.mdc"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
