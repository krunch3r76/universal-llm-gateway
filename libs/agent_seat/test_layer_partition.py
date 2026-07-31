"""Address/capability layer partition — bus store must not import capability normalizers."""

from __future__ import annotations

import ast
from pathlib import Path

_BUS_STORE = Path(__file__).resolve().parents[1] / "agent_bus_store"
_FORBIDDEN_IMPORTS = frozenset(
    {
        "normalize_agent_slug",
        "resolve_agent_model",
        "resolve_agent_provider",
        "resolve_agent_valid_family",
        "resolve_agent_model_requirement",
        "seat_to_family",
    }
)


def _module_imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            if "agent_seat" in node.module or node.module == "agent_seat":
                for alias in node.names:
                    names.add(alias.name)
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.startswith("agent_seat"):
                    names.add(alias.name.split(".")[-1])
    return names


def test_bus_store_modules_do_not_import_capability_normalizers() -> None:
    offenders: list[str] = []
    for path in sorted(_BUS_STORE.glob("*.py")):
        if path.name.startswith("test_"):
            continue
        imported = _module_imports(path)
        hit = imported & _FORBIDDEN_IMPORTS
        if hit:
            offenders.append(f"{path.name}: {sorted(hit)}")
    assert offenders == [], f"partition leak: {offenders}"


def test_bus_store_address_matchers_import_normalize_bus_address() -> None:
    for name in ("wait_status.py", "disposition.py", "recipients.py"):
        path = _BUS_STORE / name
        imported = _module_imports(path)
        assert "normalize_bus_address" in imported or "expand_recipient_slugs" in imported
