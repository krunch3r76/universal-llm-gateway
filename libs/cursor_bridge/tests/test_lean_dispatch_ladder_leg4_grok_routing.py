"""Leg 4 resolve: Live grok_routing_read + Proposed shell fallback."""

from __future__ import annotations

import os
import re
from pathlib import Path

import pytest

_SHELL_FALLBACK = re.compile(
    r'python.*-c.*cursor_bridge\.grok_window_bindings import binding_for,\s*load_routing',
    re.IGNORECASE | re.DOTALL,
)
_LIVE_SNIPPETS = (
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
    live_end = block.find("**Proposed")
    assert live_end > 0, "missing Proposed subsection after Live"
    live_body = block[:live_end]
    for snippet in _LIVE_SNIPPETS:
        assert snippet in live_body, f"missing {snippet!r} in Live block"
    assert not _SHELL_FALLBACK.search(live_body), (
        "shell binding_for/load_routing one-liner must not be in Live after AC8 flip"
    )

    assert "**Proposed (" in block or "**Proposed (legacy" in block, (
        "missing Proposed shell-fallback subsection"
    )
    proposed_body = block[live_end : live_end + 2000]
    assert _SHELL_FALLBACK.search(proposed_body), (
        "missing verified shell binding_for/load_routing one-liner in Proposed fallback"
    )
