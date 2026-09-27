"""Rendered wake opening matches the skill and runbook excerpt."""

from __future__ import annotations

import os
import pwd
from pathlib import Path

import pytest

from services.git_integration_worker.cursor_auto.operator_wake_body import (
    SKILL_RELOAD_LINE,
    render_operator_wake_body,
)

pytestmark = pytest.mark.offline

_REPO = Path(__file__).resolve().parents[2]
_SKILL = _REPO / "cursor-plugins/ulg-ecosystem/skills/cdp-operator-proxy/SKILL.md"
_OPENING_LANE = "<lane>"
_POINTER = "services/git_integration_worker/cursor_auto/operator_wake_body.py"


def _backtick_span(text: str, prefix: str) -> str:
    marker = f"`{prefix}"
    start = text.index(marker) + 1
    end = text.index("`", start)
    return text[start:end]


def _cortex_root() -> Path | None:
    env = os.environ.get("CORTEX_FILES_ROOT", "").strip()
    roots = []
    if env:
        roots.append(Path(env))
    roots.append(Path("/data/files"))
    roots.append(Path(pwd.getpwuid(os.getuid()).pw_dir) / "mcp-data" / "files")
    roots.append(Path.home() / "mcp-data" / "files")
    for root in roots:
        if (root / "notes/runbooks/maestro-loop.md").is_file():
            return root
    return None


def test_rendered_opening_equals_skill_excerpt() -> None:
    skill = _SKILL.read_text(encoding="utf-8")
    excerpt = _backtick_span(skill, "WAKE. First:")
    assert excerpt == SKILL_RELOAD_LINE.format(lane=_OPENING_LANE)
    assert _POINTER in skill
    body = render_operator_wake_body(
        owner_lane="12286",
        child_lane="12286",
        dispatch_id="dispatch",
        closeout_turn="1",
    )
    assert body.splitlines()[0] == SKILL_RELOAD_LINE.format(lane="12286")


def test_rendered_opening_equals_runbook_excerpt() -> None:
    root = _cortex_root()
    if root is None:
        pytest.skip("maestro-loop runbook not on this host")
    runbook = (root / "notes/runbooks/maestro-loop.md").read_text(encoding="utf-8")
    excerpt = _backtick_span(runbook, "WAKE. First:")
    assert excerpt == SKILL_RELOAD_LINE.format(lane=_OPENING_LANE)
    assert _POINTER in runbook
    body = render_operator_wake_body(
        owner_lane="12286",
        child_lane="13007",
        dispatch_id="dispatch",
        closeout_turn="1",
    )
    assert body.splitlines()[0] == SKILL_RELOAD_LINE.format(lane="12286")
