#!/usr/bin/env python3
"""Backfill ``sot: cortex`` frontmatter on cortex-mount agent_skill SOT files (roadmap 2.3).

Retired 2026-07-11: the ``cortex://agent-skills/`` mirror was trash-moved; SOT is
``.cursor/skills/<slug>/SKILL.md`` only.
"""

from __future__ import annotations

import argparse
import sys


def run(*, check: bool = False) -> int:
    del check
    print(
        "SKIP: cortex agent-skills mirror retired — use .cursor/skills SOT only",
        file=sys.stderr,
    )
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="Report only; do not write")
    args = parser.parse_args()
    raise SystemExit(run(check=args.check))


if __name__ == "__main__":
    main()
