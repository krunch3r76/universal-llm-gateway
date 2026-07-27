#!/usr/bin/env python3
"""P0-AC1 presence gate: materializer emitters must dual-write charter-state footer."""

from __future__ import annotations

import re
import sys
from pathlib import Path

_EMITTER_SOURCES = (
    "materializer.py",
    "materializer_autonomous.py",
    "materializer_autonomous_arc.py",
    "materializer_consult.py",
    "materializer_closed_detent.py",
)

_PATTERN = re.compile(
    r"charter-state|emit_footer|append_footer_to_packet",
    re.IGNORECASE,
)


def main() -> int:
    root = Path(__file__).resolve().parent
    missing: list[str] = []
    for name in _EMITTER_SOURCES:
        path = root / name
        text = path.read_text(encoding="utf-8")
        if not _PATTERN.search(text):
            missing.append(name)
    if missing:
        print(
            "charter-state footer missing from emitter sources:",
            ", ".join(missing),
            file=sys.stderr,
        )
        return 1
    print(f"footer presence OK ({len(_EMITTER_SOURCES)} emitters)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
