#!/usr/bin/env python3
"""CLI for the unearned-self-assertion static enumerable reporter.

Emits JSON on stdout. Exit 0 always when the report formed — findings are
data, not a gate. Exit 2 only if the reporter itself could not run.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def main() -> int:
    """Run the reporter against this checkout and print the JSON payload."""
    from unearned_self_assertion_auditor import run_reporter

    try:
        report = run_reporter(REPO_ROOT)
    except Exception as exc:  # noqa: BLE001 — CLI boundary; payload is the error
        print(json.dumps({"error": str(exc), "verdict": "could_not_check"}), flush=True)
        return 2
    print(json.dumps(report.to_dict(), indent=2, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
