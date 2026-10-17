"""A8 — declarative_probe Anthropic section must not call billed Messages endpoints."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

pytestmark = pytest.mark.offline

_PROBE_PATH = Path(__file__).resolve().parent / "declarative_probe.py"


def _anthropic_section_source() -> str:
    text = _PROBE_PATH.read_text(encoding="utf-8")
    marker = '_ANTHROPIC_API_BASE = "https://api.anthropic.com"'
    start = text.index(marker)
    end = text.index("# --------------------------------------------------------------------------- #\n# Public entry point")
    return text[start:end]


def _client_names_in_anthropic_section(tree: ast.Module) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.With):
            continue
        for item in node.items:
            if not isinstance(item.context_expr, ast.Call):
                continue
            func = item.context_expr.func
            if isinstance(func, ast.Name) and func.id == "make_sync_client":
                if item.optional_vars and isinstance(item.optional_vars, ast.Name):
                    names.add(item.optional_vars.id)
    return names


def test_declarative_probe_anthropic_calls_models_get_only() -> None:
    """Break covered: probe starts POSTing /v1/messages and spending credit off-proxy."""
    section = _anthropic_section_source()
    forbidden_strings = (
        "/v1/messages",
        "/v1/messages/batches",
        ".post(",
        ".stream(",
    )
    for token in forbidden_strings:
        assert token not in section, f"forbidden {token!r} in Anthropic probe section"

    tree = ast.parse(section)
    client_names = _client_names_in_anthropic_section(tree)
    assert client_names, "expected make_sync_client(...) binding in Anthropic section"

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if not isinstance(node.func, ast.Attribute):
            continue
        if node.func.attr != "get":
            continue
        if not isinstance(node.func.value, ast.Name):
            continue
        if node.func.value.id not in client_names:
            continue
        if not node.args:
            pytest.fail("Anthropic client .get() must pass explicit path")
        path_arg = node.args[0]
        if isinstance(path_arg, ast.Constant) and path_arg.value != "/v1/models":
            pytest.fail(
                f"Anthropic client .get() must use /v1/models, got {path_arg.value!r}"
            )
