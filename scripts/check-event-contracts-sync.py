#!/usr/bin/env python3
"""Check RAG event signal parity between code and docs.

Usage:
    python scripts/check-event-contracts-sync.py
    python scripts/check-event-contracts-sync.py --staged
"""

from __future__ import annotations

import argparse
import ast
import re
import subprocess
import sys
from pathlib import Path
from typing import cast

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DOCS_CONTRACTS = PROJECT_ROOT / "docs" / "event-contracts.md"
RAG_EVENTS_MODULE = PROJECT_ROOT / "services" / "rag" / "events.py"
RAG_EVENTS_PACKAGE = PROJECT_ROOT / "services" / "rag" / "events"
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


def _is_relevant_staged_change(staged: set[str]) -> bool:
    return (
        "docs/event-contracts.md" in staged
        or "services/rag/events.py" in staged
        or any(path.startswith("services/rag/events/") for path in staged)
    )


def _iter_event_files() -> list[Path]:
    if RAG_EVENTS_PACKAGE.exists():
        return sorted(
            [
                path
                for path in RAG_EVENTS_PACKAGE.rglob("*.py")
                if path.name != "__init__.py"
            ]
        )
    if RAG_EVENTS_MODULE.exists():
        return [RAG_EVENTS_MODULE]
    raise FileNotFoundError(
        "Could not find RAG event factories at services/rag/events.py or services/rag/events/*.py"
    )


def _extract_code_signals(event_files: list[Path]) -> set[str]:
    signals: set[str] = set()
    for file_path in event_files:
        tree = ast.parse(file_path.read_text(encoding="utf-8"), filename=str(file_path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            if not isinstance(node.func, ast.Name) or node.func.id != "Event":
                continue
            signal_kw = next(
                (kw for kw in node.keywords if kw.arg == "signal"),
                None,
            )
            if signal_kw is None:
                continue
            if isinstance(signal_kw.value, ast.Constant) and isinstance(
                signal_kw.value.value, str
            ):
                signal = signal_kw.value.value
                if signal.startswith("rag."):
                    signals.add(signal)
    return signals


def _extract_docs_signals() -> set[str]:
    text = DOCS_CONTRACTS.read_text(encoding="utf-8")
    # Restrict extraction to markdown table rows where the first column is the signal.
    pattern = re.compile(
        r"^\|\s*`(rag\.[a-z0-9_]+(?:\.[a-z0-9_]+){0,6})`\s*\|",
        flags=re.MULTILINE,
    )
    return set(pattern.findall(text))


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


def _format_signal_list(title: str, signals: set[str]) -> str:
    if not signals:
        return f"{title}: none"
    joined = "\n".join(f"  - {signal}" for signal in sorted(signals))
    return f"{title}:\n{joined}"


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate RAG event/doc signal sync")
    _ = parser.add_argument(
        "--staged",
        action="store_true",
        help="Only run when staged changes touch RAG events or event-contracts.md",
    )
    args = parser.parse_args()
    staged_mode = cast(bool, args.staged)

    if staged_mode:
        try:
            staged = _staged_paths()
        except RuntimeError as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return 1
        if not _is_relevant_staged_change(staged):
            print("OK No staged RAG event contract changes to validate")
            return 0

    code_signals = _extract_code_signals(_iter_event_files())
    docs_signals = _extract_docs_signals()
    forbidden_import_files = _find_forbidden_monolith_imports()

    code_only = code_signals - docs_signals
    docs_only = docs_signals - code_signals

    if code_only or docs_only or forbidden_import_files:
        print("❌ RAG event contracts out of sync")
        print(_format_signal_list("In code only", code_only))
        print(_format_signal_list("In docs only", docs_only))
        if forbidden_import_files:
            print("Deprecated monolith imports found:")
            for file_path in forbidden_import_files:
                print(f"  - {file_path}")
            print(
                "Use explicit domain modules: services.rag.events.lifecycle|"
                "extraction|indexing|query"
            )
        print(
            "\nUpdate docs/event-contracts.md and event factories in the same change."
        )
        return 1

    print(f"✅ RAG event contracts in sync ({len(code_signals)} signals)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
