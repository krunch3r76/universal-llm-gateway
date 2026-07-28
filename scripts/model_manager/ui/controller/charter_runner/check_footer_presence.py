#!/usr/bin/env python3
"""P0-AC1 presence gate: emitters dual-write inbound footer; packets state return contract."""

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

_EMITTER_PATTERN = re.compile(
    r"charter-state|emit_footer|append_footer_to_packet",
    re.IGNORECASE,
)

_PACKET_TEMPLATE_NAMES = (
    "generate.md",
    "autonomous.md",
    "consult.md",
    "closed_detent.md",
)

_PACKET_RETURN_PATTERN = re.compile(
    r"```\s*charter-state\s*```.*schema_version.*next_pickup.*window_id",
    re.IGNORECASE | re.DOTALL,
)


def _scan_emitters(root: Path) -> tuple[list[str], list[str]]:
    ok: list[str] = []
    missing: list[str] = []
    for name in _EMITTER_SOURCES:
        path = root / name
        text = path.read_text(encoding="utf-8")
        if _EMITTER_PATTERN.search(text):
            ok.append(name)
        else:
            missing.append(name)
    return ok, missing


def _scan_packet_templates(root: Path) -> tuple[list[str], list[str]]:
    ok: list[str] = []
    missing: list[str] = []
    packets = root / "packets"
    for name in _PACKET_TEMPLATE_NAMES:
        path = packets / name
        text = path.read_text(encoding="utf-8")
        if _PACKET_RETURN_PATTERN.search(text):
            ok.append(f"packets/{name}")
        else:
            missing.append(f"packets/{name}")
    return ok, missing


def main() -> int:
    root = Path(__file__).resolve().parent
    emitter_ok, emitter_missing = _scan_emitters(root)
    packet_ok, packet_missing = _scan_packet_templates(root)

    for path in emitter_ok:
        print(f"emitter OK: {path}")
    for path in packet_ok:
        print(f"packet OK: {path}")

    failures: list[str] = []
    if emitter_missing:
        failures.extend(f"emitter missing footer dual-write: {name}" for name in emitter_missing)
    if packet_missing:
        failures.extend(
            f"packet missing charter-state return contract: {name}"
            for name in packet_missing
        )

    if failures:
        for line in failures:
            print(line, file=sys.stderr)
        return 1

    print(
        "footer presence OK "
        f"({len(emitter_ok)} emitters + {len(packet_ok)} packet templates)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
