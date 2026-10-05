"""Repo grep: only MCP cortex.py may claim adapter attribution on dispatch."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.offline

_REPO_ROOT = Path(__file__).resolve().parents[2]
_ALLOWED_ADAPTER = _REPO_ROOT / "services/mcp-server/tools/cortex.py"
_SERVER_SIDE_EXEMPT = {
    _REPO_ROOT / "libs/cortex_store/routes/dispatch.py",
    _REPO_ROOT / "libs/cortex_store/dispatch_ops/__init__.py",
    _REPO_ROOT / "libs/ulg_routing_headers/__init__.py",
    _REPO_ROOT / "libs/cortex_store/openapi_mcp/death_path.py",
}
_MCP_ADAPTER_TEST_EXEMPT = {
    _REPO_ROOT / "services/mcp-server/tools/test_cortex_routing_headers.py",
    _REPO_ROOT / "services/mcp-server/tools/test_agent_bus_friction_file_verb.py",
    _REPO_ROOT / "services/mcp-server/tools/test_agent_bus_graph_write_verb.py",
}
_BODY_ADAPTER = re.compile(r"""["']via_adapter["']\s*:\s*True\b""")
_KW_ADAPTER = re.compile(r"\bvia_adapter\s*=\s*True\b")
_HEADER_ADAPTER = re.compile(r"""["']X-ULG-Adapter["']""")


def _is_test_path(path: Path) -> bool:
    rel = path.relative_to(_REPO_ROOT)
    if rel.name.startswith("test_") and rel.suffix == ".py":
        return True
    return "tests" in rel.parts


def _scan_py_files() -> list[Path]:
    skip_dirs = {".git", "tmp", "node_modules", "__pycache__", ".venv"}
    paths: list[Path] = []
    for path in _REPO_ROOT.rglob("*.py"):
        if any(part in skip_dirs for part in path.parts):
            continue
        paths.append(path)
    return paths


def test_only_mcp_adapter_claims_adapter() -> None:
    violations: list[str] = []
    for path in _scan_py_files():
        if path in _SERVER_SIDE_EXEMPT or path in _MCP_ADAPTER_TEST_EXEMPT:
            continue
        if _is_test_path(path):
            continue
        if path == _ALLOWED_ADAPTER:
            continue
        text = path.read_text(encoding="utf-8")
        for lineno, line in enumerate(text.splitlines(), start=1):
            if _BODY_ADAPTER.search(line) or _KW_ADAPTER.search(line):
                violations.append(f"{path.relative_to(_REPO_ROOT)}:{lineno}: {line.strip()}")
            if _HEADER_ADAPTER.search(line) and path != _ALLOWED_ADAPTER:
                violations.append(f"{path.relative_to(_REPO_ROOT)}:{lineno}: {line.strip()}")
    assert not violations, "adapter claims outside cortex.py:\n" + "\n".join(violations)
