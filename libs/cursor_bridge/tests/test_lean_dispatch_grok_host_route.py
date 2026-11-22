"""Doc-contract: Grok host-command routing (lean-dispatch + ladder + runbook walls)."""

from __future__ import annotations

import os
import re
from pathlib import Path

import pytest

from agent_seat.profiles import CAPABILITY_TOKENS

_REPO_ROOT = Path(__file__).resolve().parents[3]
_SKILL = (
    _REPO_ROOT
    / "cursor-plugins/ulg-ecosystem/skills/lean-dispatch/SKILL.md"
)
_RUNBOOK_CANDIDATES = (
    Path(os.environ.get("CORTEX_FILES_ROOT", ""))
    / "notes/system/runbooks/grok-host-preflight.md",
    Path.home() / ".cortex/files/notes/system/runbooks/grok-host-preflight.md",
    Path("/data/files/notes/system/runbooks/grok-host-preflight.md"),
)
_LADDER_CANDIDATES = (
    Path(os.environ.get("CORTEX_FILES_ROOT", ""))
    / "notes/system/templates/lean-dispatch-ladder.md",
    Path.home() / ".cortex/files/notes/system/templates/lean-dispatch-ladder.md",
    Path("/data/files/notes/system/templates/lean-dispatch-ladder.md"),
)


def _first_existing(paths: tuple[Path, ...]) -> Path | None:
    for path in paths:
        if path.is_file():
            return path
    return None


def test_capability_tokens_no_host_exec() -> None:
    assert "host_exec" not in CAPABILITY_TOKENS


def test_lean_dispatch_grok_host_route_skill() -> None:
    text = _SKILL.read_text(encoding="utf-8")
    section_start = text.find("## Grok Bot seats")
    assert section_start >= 0
    duty = text.find("## Duty → wire", section_start)
    block = text[section_start:duty]
    for needle in (
        "Host-command route",
        "**A**",
        "**B**",
        "**C**",
        "box shell",
        "host grant",
        "lane B",
        "runbook:grok-host-preflight",
        "PREFLIGHT",
        "grok_routing",
    ):
        assert needle in block, f"missing {needle!r} in Grok Bot seats block"
    assert "host_exec" not in block


def test_grok_host_preflight_runbook_headings() -> None:
    path = _first_existing(_RUNBOOK_CANDIDATES)
    if path is None:
        pytest.skip("grok-host-preflight.md not on disk; cortex runbook check deferred")
    text = path.read_text(encoding="utf-8")
    for heading in (
        "Grant conditions",
        "Three probes",
        "PREFLIGHT bus receipt",
        "Freshness",
        "Demonstration job J",
        "grant",
        "checkout",
        "interpreter",
        "PASS",
        "FAIL",
        "raw stdout",
        "as_of",
    ):
        assert heading in text, f"missing {heading!r} in runbook"


def test_lean_dispatch_ladder_grok_host_preflight_step() -> None:
    path = _first_existing(_LADDER_CANDIDATES)
    if path is None:
        pytest.skip("lean-dispatch-ladder.md not on disk; cortex template check deferred")
    text = path.read_text(encoding="utf-8")
    assert "Leg 2c — Grok host PREFLIGHT" in text
    idx = text.find("Leg 2c — Grok host PREFLIGHT")
    block = text[idx : idx + 2500]
    for needle in (
        "runbook:grok-host-preflight",
        "PREFLIGHT",
        "box shell",
        "host grant",
        "lane B",
        "**A**",
        "**B**",
        "**C**",
        "grok_routing",
    ):
        assert needle in block, f"missing {needle!r} in Leg 2c block"
    assert not re.search(r"grok_routing.*grant state", block, re.I)