"""libs/cursor_bridge must never touch clipboard or CDP keyboard paste paths."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest
from cursor_bridge.dom_composer_paste import _set_composer_body

pytestmark = pytest.mark.offline

_PKG = Path(__file__).resolve().parent.parent
_CDP_INPUT_ALLOWED = frozenset({"cdp_composer_input.py"})
_PROD_PY = sorted(
    p
    for p in _PKG.glob("*.py")
    if not p.name.startswith("test_") and p.name not in _CDP_INPUT_ALLOWED
)
_FORBIDDEN_SUBSTRINGS = (
    "navigator.clipboard",
    "xclip",
    "xdotool",
    "pyperclip",
)
_FORBIDDEN_INPUT = frozenset({"Input.insertText", "Input.dispatchKeyEvent"})


def test_cursor_bridge_prod_sources_no_clipboard_or_os_paste_tools() -> None:
    hits: list[str] = []
    for path in _PROD_PY:
        text = path.read_text(encoding="utf-8")
        if "clipboard" in text.lower():
            hits.append(f"{path.name}: clipboard")
        for token in _FORBIDDEN_SUBSTRINGS:
            if token in text:
                hits.append(f"{path.name}: {token}")
    assert not hits, hits


def test_cursor_bridge_prod_sources_no_cdp_keyboard_injection_literals() -> None:
    hits: list[str] = []
    for path in _PROD_PY:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                for forbidden in _FORBIDDEN_INPUT:
                    if forbidden in node.value:
                        hits.append(f"{path.name}:{node.lineno}: {forbidden}")
    assert not hits, hits


def test_cdp_composer_input_uses_page_scoped_input_only() -> None:
    path = _PKG / "cdp_composer_input.py"
    text = path.read_text(encoding="utf-8")
    assert "Input.insertText" in text
    assert "Input.dispatchKeyEvent" in text
    assert "xdotool" not in text
    assert "navigator.clipboard" not in text


def test_dom_set_composer_expression_does_not_focus() -> None:
    expr = _set_composer_body('[aria-label="Prompt"]', "hello")
    assert ".focus(" not in expr
    assert "Input.insertText" not in expr
