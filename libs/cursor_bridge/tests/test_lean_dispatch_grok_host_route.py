"""Doc-contract: Grok host-command routing (lean-dispatch + ladder + runbook walls)."""

from __future__ import annotations

import os
import re
from pathlib import Path

import pytest
from agent_seat.profiles import CAPABILITY_TOKENS

_REPO_ROOT = Path(__file__).resolve().parents[3]
_SKILL = _REPO_ROOT / "cursor-plugins/ulg-ecosystem/skills/lean-dispatch/SKILL.md"
_LEAN_LADDER = _REPO_ROOT / "cursor-plugins/ulg-ecosystem/commands/lean-ladder.md"
_RUNBOOK_CANDIDATES = (
    Path(os.environ.get("CORTEX_FILES_ROOT", ""))
    / "notes/system/runbooks/grok-host-preflight.md",
    Path.home() / ".cortex/files/notes/system/runbooks/grok-host-preflight.md",
    Path("/mnt/torus/mcp-data/files/notes/system/runbooks/grok-host-preflight.md"),
    Path("/data/files/notes/system/runbooks/grok-host-preflight.md"),
)
_BOOT_CANDIDATES = (
    Path(os.environ.get("CORTEX_FILES_ROOT", ""))
    / "notes/system/runbooks/grok-bot-boot.md",
    Path.home() / ".cortex/files/notes/system/runbooks/grok-bot-boot.md",
    Path("/mnt/torus/mcp-data/files/notes/system/runbooks/grok-bot-boot.md"),
    Path("/data/files/notes/system/runbooks/grok-bot-boot.md"),
)
_LADDER_CANDIDATES = (
    Path(os.environ.get("CORTEX_FILES_ROOT", ""))
    / "notes/system/templates/lean-dispatch-ladder.md",
    Path.home() / ".cortex/files/notes/system/templates/lean-dispatch-ladder.md",
    Path("/mnt/torus/mcp-data/files/notes/system/templates/lean-dispatch-ladder.md"),
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
        "assigned lane-B worktree",
        "runbook:grok-host-preflight",
        "grok-host-preflight",
        "fs_read",
        "PREFLIGHT",
        "grok_routing",
    ):
        assert needle in block, f"missing {needle!r} in Grok Bot seats block"
    assert "host_exec" not in block


def test_lean_ladder_grok_host_preflight_pointer() -> None:
    text = _LEAN_LADDER.read_text(encoding="utf-8")
    for needle in (
        "Leg 2c — Grok host PREFLIGHT",
        "runbook:grok-host-preflight",
        "grok-host-preflight.md",
    ):
        assert needle in text, f"missing {needle!r} in lean-ladder command"


def test_grok_bot_boot_grok_host_preflight_pointer() -> None:
    path = _first_existing(_BOOT_CANDIDATES)
    if path is None:
        pytest.skip("grok-bot-boot.md not on disk; cortex runbook check deferred")
    text = path.read_text(encoding="utf-8")
    assert "runbook:grok-host-preflight" in text
    assert "grok-host-preflight.md" in text
    assert "lean-ladder" in text or "Leg 2c" in text


def test_grok_host_preflight_runbook_headings() -> None:
    path = _first_existing(_RUNBOOK_CANDIDATES)
    if path is None:
        pytest.skip("grok-host-preflight.md not on disk; cortex runbook check deferred")
    text = path.read_text(encoding="utf-8")
    for heading in (
        "Grant conditions",
        "Host checkout execution",
        "Project venv",
        "Module origin pin",
        "Share URI",
        "Assigned worktree resolution",
        "Checkout isolation",
        "Temporary scripts",
        "Entry-point loaders",
        "route-C host grant",
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
        ".venvs/universal",
        "workspaces://",
        "/mnt/torus/projects/",
        "cortex://",
        "lean-dispatch",
        "lean-ladder",
        "grok-bot-boot",
        "GIT_INTEGRATION_WORKTREE_ROOT",
        "lane-{thread",
    ):
        assert heading in text, f"missing {heading!r} in runbook"


def test_grok_host_preflight_module_origin_pin_contract() -> None:
    path = _first_existing(_RUNBOOK_CANDIDATES)
    if path is None:
        pytest.skip("grok-host-preflight.md not on disk; cortex runbook check deferred")
    text = path.read_text(encoding="utf-8")
    pin_idx = text.find("## Module origin pin")
    assert pin_idx >= 0
    pin_block = text[pin_idx : pin_idx + 2200]
    for needle in (
        "not sufficient",
        "repo-owned",
        "__file__",
        "importlib",
        "PYTHONPATH",
        "lane A",
        "refuse",
        "Recheck",
        "Composer",
        "sys.executable",
        ".venvs/universal/bin/python",
        "agent_seat",
        "cursor_bridge",
        "lane-{thread_id}",
    ):
        assert needle in pin_block, f"missing origin-pin needle {needle!r}"
    host_idx = text.find("## Host checkout execution")
    assert host_idx >= 0
    assert "Module origin pin" in text[host_idx : host_idx + 1200]
    temp_idx = text.find("## Temporary scripts")
    assert temp_idx >= 0
    assert "Module origin pin" in text[temp_idx : temp_idx + 1200]
    interp_idx = text.find("### 3. Interpreter")
    assert interp_idx >= 0
    interp_block = text[interp_idx : interp_idx + 900]
    for needle in (
        "Module origin pin",
        "__file__",
        "lane A",
        "PYTHONPATH",
        "sys.executable",
    ):
        assert needle in interp_block, f"missing interpreter origin needle {needle!r}"


def test_grok_host_preflight_worktree_isolation_contract() -> None:
    path = _first_existing(_RUNBOOK_CANDIDATES)
    if path is None:
        pytest.skip("grok-host-preflight.md not on disk; cortex runbook check deferred")
    text = path.read_text(encoding="utf-8")
    for needle in (
        "lane A",
        "refuse",
        "team_dispatch",
        "outside any checkout",
        "lookup roots",
        "not default write",
        "cursor-sdk/lane-",
        "rule:share-uri-local-map_ulg",
        "share-uri-local-map_ulg.mdc",
        "Visible mount ≠ grant",
        "read/lookup",
        "also bind Cursor host execution",
        "access preference chooses tools",
        "governs writes for Cursor and Grok alike",
        "cortex://` durable edits go through MCP `fs`",
    ):
        assert needle in text, f"missing isolation needle {needle!r} in runbook"
    for forbidden in (
        "hash mismatch vs hub read",
        "sha256sum",
        "hub checkout or the commissioned lane-B",
    ):
        assert forbidden not in text, f"forbidden hub-scoped phrase {forbidden!r} still in runbook"


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


_FORBIDDEN_RUNBOOK_STRINGS = (
    "explicitly names that exception",
    "when the job requires an isolated lane",
    "untracked scratch in the tree is acceptable",
)


def test_grok_host_preflight_runbook_kaywan58_walls() -> None:
    path = _first_existing(_RUNBOOK_CANDIDATES)
    if path is None:
        pytest.skip("grok-host-preflight.md not on disk; cortex runbook check deferred")
    text = path.read_text(encoding="utf-8")
    for forbidden in _FORBIDDEN_RUNBOOK_STRINGS:
        assert forbidden not in text, f"forbidden runbook string present: {forbidden!r}"
    idx = text.find("### 2. Checkout")
    assert idx >= 0
    checkout_block = text[idx : idx + 600]
    fail_m = re.search(r"^- \*\*Fail:\*\*(.+)$", checkout_block, re.M)
    assert fail_m is not None
    fail_line = fail_m.group(1)
    assert "toplevel is lane A / shared hub checkout" in fail_line
    assert "when the job requires an isolated lane" not in fail_line
    temp_idx = text.find("## Temporary scripts")
    assert temp_idx >= 0
    temp_block = text[temp_idx : temp_idx + 1200]
    assert "Visible `??`" in temp_block
    assert "external temp" in temp_block
    assert "assigned lane" in temp_block
    assert "verified host grant" in temp_block
    assert "Composer" in temp_block
    assert "does not authorize Cursor" not in text
