"""Fail when mcp-server docstrings teach job= as a team_dispatch parameter.

Breaks when a ``team_dispatch(...)`` example in a non-test module docstring
still names ``job=`` (retired generate/handoff wire). Pipeline ``options``
``job=`` is a different parameter and must not match.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

_MCP_SERVER = Path(__file__).resolve().parents[1]
_TEAM_DISPATCH_JOB_PARAM = re.compile(
    r"team_dispatch\s*\((?:[^)]|\n)*?\bjob\s*=",
    re.IGNORECASE,
)


def _iter_module_py(root: Path) -> list[Path]:
    out: list[Path] = []
    for path in sorted(root.rglob("*.py")):
        name = path.name
        if name.startswith("test_") or name == "conftest.py":
            continue
        if any(part.startswith(".") for part in path.parts):
            continue
        out.append(path)
    return out


def _docstrings(source: str) -> list[str]:
    tree = ast.parse(source)
    found: list[str] = []
    for node in ast.walk(tree):
        if isinstance(
            node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
        ):
            doc = ast.get_docstring(node, clean=False)
            if doc:
                found.append(doc)
    return found


@pytest.mark.offline
def test_mcp_server_docstrings_do_not_name_job_as_team_dispatch_param() -> None:
    offenders: list[str] = []
    for path in _iter_module_py(_MCP_SERVER):
        text = path.read_text(encoding="utf-8")
        try:
            docs = _docstrings(text)
        except SyntaxError:
            continue
        for doc in docs:
            if _TEAM_DISPATCH_JOB_PARAM.search(doc):
                offenders.append(str(path.relative_to(_MCP_SERVER)))
                break
    assert offenders == [], (
        "mcp-server docstring still names job= as a team_dispatch parameter: "
        + ", ".join(offenders)
    )
