#!/usr/bin/env -S python3.12
"""Agent-bus provenance standing gate: the paths this repo claims for the
agent-bus service must exist on disk.

V1 scope: existence checks only (README, library SOT). Extend with real
provenance invariants (dispatch_links integrity, checkpoint lineage) once
libs/agent_bus_store/tests/test_provenance_invariants.py lands (7404) —
this script may then shell out to that module instead of a bare path list.

Exit 0 if clean, 1 if drift. Run via scripts/hooks/pre_commit.sh.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

REQUIRED_PATHS = (
    "services/agent-bus/README.md",
    "libs/agent_bus_store/__init__.py",
)


def main() -> int:
    missing = [p for p in REQUIRED_PATHS if not (REPO_ROOT / p).is_file()]
    if missing:
        for p in missing:
            print(f"FAIL check-agent-bus-standing: missing {p}", file=sys.stderr)
        return 1
    print("OK check-agent-bus-standing: agent-bus provenance paths present")
    return 0


if __name__ == "__main__":
    sys.exit(main())
