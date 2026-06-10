#!/usr/bin/env python3
"""Guard against deprecated monolithic services.rag.events imports.

Usage:
    python scripts/check-rag-events-imports.py
    python scripts/check-rag-events-imports.py --staged
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path
from typing import cast

PROJECT_ROOT = Path(__file__).resolve().parents[1]
RAG_ROOT = PROJECT_ROOT / "services" / "rag"


def _staged_paths() -> set[str]:
    result = subprocess.run(
        ["git", "diff", "--cached", "--name-only", "--diff-filter=ACMR"],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(f"git diff failed: {result.stderr.strip()}")
    return {line.strip() for line in result.stdout.splitlines() if line.strip()}


def _find_forbidden_monolith_imports() -> list[str]:
    """Find imports that use the deprecated monolithic events surface."""
    violations: list[str] = []
    patterns = (
        re.compile(r"^\s*from\s+services\.rag\.events\s+import\s+", re.MULTILINE),
        re.compile(r"^\s*import\s+services\.rag\.events(?:\s|$)", re.MULTILINE),
    )
    for file_path in sorted(RAG_ROOT.rglob("*.py")):
        text = file_path.read_text(encoding="utf-8")
        if any(pattern.search(text) for pattern in patterns):
            violations.append(str(file_path.relative_to(PROJECT_ROOT)))
    return violations


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Guard against deprecated monolithic services.rag.events imports"
    )
    _ = parser.add_argument(
        "--staged",
        action="store_true",
        help="Only run when staged changes touch services/rag/events",
    )
    args = parser.parse_args()
    staged_mode = cast(bool, args.staged)

    if staged_mode:
        try:
            staged = _staged_paths()
        except RuntimeError as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return 1
        if not any(p.startswith("services/rag/events") for p in staged):
            print("OK No staged RAG events changes to validate")
            return 0

    forbidden_import_files = _find_forbidden_monolith_imports()
    if forbidden_import_files:
        print("❌ Deprecated monolith imports found:")
        for file_path in forbidden_import_files:
            print(f"  - {file_path}")
        print(
            "Use explicit domain modules: services.rag.events.lifecycle|"
            "extraction|indexing|query"
        )
        return 1

    print("✅ No deprecated services.rag.events monolith imports")
    return 0


if __name__ == "__main__":
    sys.exit(main())
