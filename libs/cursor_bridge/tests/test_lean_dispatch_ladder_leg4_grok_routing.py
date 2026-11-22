"""Leg 4 resolve block must use grok_routing_read, not shell load_routing one-liner."""

from __future__ import annotations

import os
import re
from pathlib import Path

import pytest

_FORBIDDEN = re.compile(
    r'python.*-c.*cursor_bridge\.grok_window_bindings import binding_for,\s*load_routing',
    re.IGNORECASE | re.DOTALL,
)
_REQUIRED_SNIPPETS = (
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
    block = text[resolve_idx : resolve_idx + 2500]
    assert _FORBIDDEN.search(block) is None, "shell python -c load_routing one-liner still present"
    for snippet in _REQUIRED_SNIPPETS:
        assert snippet in block, f"missing {snippet!r} in Leg 4 resolve block"