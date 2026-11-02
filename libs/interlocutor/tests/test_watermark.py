"""Watermark constant and wrap tests."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest
from interlocutor import watermark

pytestmark = pytest.mark.offline


def test_watermark_phrases_match_assertion_41171() -> None:
    assert watermark.OPERATOR_HEADER == "Operator request — Kaywan, relayed on his behalf"
    assert watermark.WATERMARK == "Pasted on Kaywan's behalf by Orion — AI-assisted"


def test_wrap_operator_message_includes_pinned_phrases() -> None:
    msg = watermark.wrap_operator_message(
        text="body",
        authorization_source="agent-bus:1#1",
        scope="test",
    )
    assert watermark.OPERATOR_HEADER in msg
    assert watermark.WATERMARK in msg
    assert "Source: agent-bus:1#1" in msg
    assert "body" in msg


def test_operator_paste_does_not_assign_watermark_constant() -> None:
    path = Path(__file__).resolve().parents[2] / "cursor_bridge" / "operator_paste.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    assigns = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Assign)
        and any(isinstance(t, ast.Name) and t.id == "WATERMARK" for t in node.targets)
    ]
    assert assigns == []
