"""Boot identity import boundary — registry must not leak into unrelated modules."""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Any

import pytest

_BOOT_IDENTITY_IMPORTERS = {
    "libs/agent_seat/boot_identity.py",
    "services/mcp-server/tools/cortex_named_tools/_boot_seat_resolve.py",
    "services/mcp-server/tools/cortex_named_tools/_orchestration_tools.py",
    "services/mcp-server/tools/cortex_named_tools/_boot_runner.py",
    "services/mcp-server/tools/cortex_named_tools/_boot_data_fetch.py",
    "services/mcp-server/tools/_boot_helpers/_briefing_card.py",
    "services/mcp-server/tools/_boot_helpers/_manifest.py",
}


def _imports_boot_identity(tree: ast.AST) -> bool:
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if module == "agent_seat.boot_identity":
                return True
            if module == "agent_seat" and any(
                alias.name == "boot_identity" for alias in node.names
            ):
                return True
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "agent_seat.boot_identity" or alias.name.startswith(
                    "agent_seat.boot_identity."
                ):
                    return True
    return False


def test_boot_identity_import_boundary_excludes_binding() -> None:
    """Breaks when binding or another non-boot module starts reading boot identity."""
    repo = Path(__file__).resolve().parents[3]
    offenders: list[str] = []
    for root in (repo / "libs", repo / "services"):
        for path in root.rglob("*.py"):
            relative = path.relative_to(repo).as_posix()
            if "/tests/" in f"/{relative}" or path.name.startswith("test_"):
                continue
            if any(part in {".git", "__pycache__", "tmp"} for part in path.parts):
                continue
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"))
            except SyntaxError:
                continue
            if (
                _imports_boot_identity(tree)
                and relative not in _BOOT_IDENTITY_IMPORTERS
            ):
                offenders.append(relative)
    assert offenders == []


def test_wake_binding_unchanged_without_boot_registry(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Breaks when boot identity resolution starts affecting wake delivery bindings."""
    monkeypatch.setenv("AGENT_BUS_DB_PATH", str(tmp_path / "bus.db"))
    from agent_bus_store.db import init_db
    from agent_bus_store.db.endpoint_wake_ledger import resolve_wake_delivery_binding

    init_db()

    def snap() -> Any:
        payload = dict(resolve_wake_delivery_binding(house_id="21207"))
        payload.pop("as_of", None)
        return payload

    baseline = snap()
    assert snap() == baseline
