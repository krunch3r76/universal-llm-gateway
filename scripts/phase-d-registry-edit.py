"""Phase D registry edit — add mcp_claude to seat_visibility for all 65 tools.

Uses ruamel.yaml round-trip to preserve comments, formatting, and ordering.
Inserts "mcp_claude" between "mcp" and "mcp_grok" in seat_visibility lists.

Usage:
    ~/.venvs/universal/bin/python scripts/phase-d-registry-edit.py
"""

from __future__ import annotations

import sys
from pathlib import Path

try:
    from ruamel.yaml import YAML
except ImportError:
    print("ruamel.yaml not found — install: pip install ruamel.yaml", file=sys.stderr)
    sys.exit(1)

_REPO_ROOT = Path(__file__).resolve().parent.parent
_CANONICAL = _REPO_ROOT / "config" / "mcp" / "canonical.yaml"

_INSERT_TOKEN = "mcp_claude"
_ANCHOR_BEFORE = "mcp_grok"
_ANCHOR_AFTER = "mcp"


def main() -> None:
    yaml = YAML()
    yaml.preserve_quotes = True

    data = yaml.load(_CANONICAL)
    tools: list = data.get("tools", [])

    modified = 0
    skipped = 0

    for tool in tools:
        sv = tool.get("seat_visibility")
        if sv is None:
            skipped += 1
            continue
        sv_list: list = list(sv)
        if _INSERT_TOKEN in sv_list:
            skipped += 1
            continue
        # Insert mcp_claude between mcp and mcp_grok.
        # ∀ position: if mcp_grok present, insert before it; else append.
        if _ANCHOR_BEFORE in sv_list:
            idx = sv_list.index(_ANCHOR_BEFORE)
            sv_list.insert(idx, _INSERT_TOKEN)
        else:
            sv_list.append(_INSERT_TOKEN)
        # Rebuild the sequence in-place so ruamel preserves flow-style.
        sv.clear()
        for item in sv_list:
            sv.append(item)
        modified += 1

    yaml.dump(data, _CANONICAL)
    print(f"Done: {modified} entries modified, {skipped} skipped.")
    print(f"Output: {_CANONICAL}")


if __name__ == "__main__":
    main()
