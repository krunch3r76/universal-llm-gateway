#!/usr/bin/env python3
"""Backfill ``sot: cortex`` frontmatter on cortex-mount agent_skill SOT files (roadmap 2.3)."""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent.parent
if str(_REPO / "libs") not in sys.path:
    sys.path.insert(0, str(_REPO / "libs"))

from claude_bundles.resolver import CORTEX_SOT_ONLY_SLUGS, CORTEX_SOT_ROOT  # noqa: E402

_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---", re.DOTALL)
_EXTRA_SOT_SLUGS = frozenset({"matter-discipline-pattern"})


def _target_slugs() -> frozenset[str]:
    return CORTEX_SOT_ONLY_SLUGS | _EXTRA_SOT_SLUGS


def _has_sot_cortex(text: str) -> bool:
    match = _FRONTMATTER_RE.match(text)
    if not match:
        return False
    for line in match.group(1).splitlines():
        if line.strip().startswith("sot:"):
            return line.split(":", 1)[1].strip().strip("\"'") == "cortex"
    return False


def _ensure_sot_cortex(text: str) -> tuple[str, bool]:
    if _has_sot_cortex(text):
        return text, False
    match = _FRONTMATTER_RE.match(text)
    if match:
        block = match.group(1).rstrip()
        if block:
            block = f"{block}\nsot: cortex"
        else:
            block = "sot: cortex"
        new_text = f"---\n{block}\n---{text[match.end():]}"
        return new_text, True
    return f"---\nsot: cortex\n---\n{text}", True


def run(*, check: bool = False) -> int:
    root = CORTEX_SOT_ROOT
    if not root.is_dir():
        print(f"ERROR: cortex SOT root missing: {root}", file=sys.stderr)
        return 1
    changed: list[str] = []
    missing: list[str] = []
    for slug in sorted(_target_slugs()):
        path = root / f"{slug}.md"
        if not path.is_file():
            missing.append(slug)
            continue
        text = path.read_text(encoding="utf-8")
        new_text, did_change = _ensure_sot_cortex(text)
        if did_change:
            changed.append(slug)
            if not check:
                path.write_text(new_text, encoding="utf-8")
    if missing:
        print("MISSING:", ", ".join(missing), flush=True)
    if changed:
        prefix = "WOULD UPDATE" if check else "UPDATED"
        print(f"{prefix}:", ", ".join(changed), flush=True)
        return 0 if check else 0
    print("OK sot-frontmatter", flush=True)
    return 1 if missing else 0


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="Report only; do not write")
    args = parser.parse_args()
    raise SystemExit(run(check=args.check))


if __name__ == "__main__":
    main()
