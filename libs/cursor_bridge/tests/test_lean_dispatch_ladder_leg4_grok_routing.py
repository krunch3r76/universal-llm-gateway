"""Leg 4 resolve: Live shell recipe + Proposed grok_routing_read (not MCP-only live)."""

from __future__ import annotations

import os
import re
from pathlib import Path

import pytest

_SHELL_LIVE = re.compile(
    r'python.*-c.*cursor_bridge\.grok_window_bindings import binding_for,\s*load_routing',
    re.IGNORECASE | re.DOTALL,
)
_PROPOSED_SNIPPETS = (
    "grok_routing_read",
    "grok_bot_home.display",
    "preferred_spawn_present",
)


def _resolve_cortex_template() -> Path | None:
    env = os.environ.get("CORTEX_FILES_ROOT")
    if env:
        candidate = Path(env) / "notes/system/templates/lean-dispatch-ladder.md"
        if candidate.is_file():
            return candidate
    home = Path.home() / ".cortex/files/notes/system/templates/lean-dispatch-ladder.md"
    if home.is_file():
        return home
    data = Path("/data/files/notes/system/templates/lean-dispatch-ladder.md")
    if data.is_file():
        return data
    return None


def test_leg4_template_contract() -> None:
    path = _resolve_cortex_template()
    if path is None:
        pytest.skip("lean-dispatch-ladder.md not on disk; cortex template check deferred")
    text = path.read_text(encoding="utf-8")
    leg4_idx = text.find("Leg 4 — Pre-land review")
    assert leg4_idx >= 0
    resolve_idx = text.find("**Resolve `<grok_display>`**", leg4_idx)
    assert resolve_idx >= 0
    block = text[resolve_idx : resolve_idx + 4500]

    assert "**Live (current callers" in block, "missing Live resolve subsection"
    assert _SHELL_LIVE.search(block), "missing verified shell binding_for/load_routing one-liner in Live block"

    assert "**Proposed (" in block or "**Proposed (reviewed" in block, (
        "missing Proposed MCP candidate subsection"
    )
    propose_idx = block.find("**Proposed")
    assert propose_idx >= 0
    proposed_body = block[propose_idx : propose_idx + 2000]
    for snippet in _PROPOSED_SNIPPETS:
        assert snippet in proposed_body, f"missing {snippet!r} in Proposed block"
