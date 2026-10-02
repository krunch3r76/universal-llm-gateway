"""Guard: rendered operator-prompt team_dispatch examples match the MCP signature.

Breaks when an operator-facing team_dispatch(...) example names a keyword
the ulg-code tool does not take (job, session). frontier.py maps
contract->job and purpose->session on the Stargate wire; the MCP schema
rejects the wire names. The signature is read from the async def, not a
hand list.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from claude_bundles.operator_proxy_mission import ensure_operator_proxy_mission_prompt

pytestmark = pytest.mark.offline

_REPO = Path(__file__).resolve().parents[2]
_FRONTIER = _REPO / "services/mcp-server/tools/frontier.py"


def _mcp_team_dispatch_param_names() -> set[str]:
    tree = ast.parse(_FRONTIER.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "team_dispatch":
            return {arg.arg for arg in node.args.args}
    raise AssertionError(f"team_dispatch async def not found in {_FRONTIER}")


def _call_bodies(text: str) -> list[str]:
    needle = "team_dispatch("
    bodies: list[str] = []
    start = 0
    while True:
        index = text.find(needle, start)
        if index < 0:
            break
        cursor = index + len(needle)
        depth = 1
        while cursor < len(text) and depth:
            char = text[cursor]
            if char == "(":
                depth += 1
            elif char == ")":
                depth -= 1
            cursor += 1
        if depth:
            raise AssertionError("unclosed team_dispatch( in rendered prompt")
        bodies.append(text[index + len(needle) : cursor - 1])
        start = cursor
    return bodies


def _keywords(body: str) -> list[str]:
    """Top-level name= keywords. Brace and string interiors are not args."""
    names: list[str] = []
    depth = 0
    in_string: str | None = None
    escape = False
    token: list[str] = []
    for char in body:
        if in_string is not None:
            if escape:
                escape = False
            elif char == "\\":
                escape = True
            elif char == in_string:
                in_string = None
            token = []
            continue
        if char in {'"', "'"}:
            in_string = char
            token = []
            continue
        if char in "{[":
            depth += 1
            token = []
            continue
        if char in "}]":
            depth = max(0, depth - 1)
            token = []
            continue
        if depth == 0 and char == "=" and token:
            name = "".join(token).strip()
            if name.isidentifier():
                names.append(name)
            token = []
            continue
        if char.isalnum() or char == "_":
            token.append(char)
        else:
            token = []
    return names


def test_rendered_team_dispatch_keywords_match_mcp_signature() -> None:
    rendered = ensure_operator_proxy_mission_prompt("# Mission\n")
    bodies = _call_bodies(rendered)
    assert bodies, "rendered operator prompt has no team_dispatch( example"
    allowed = _mcp_team_dispatch_param_names()
    assert allowed, "frontier.py team_dispatch signature was empty"
    unknown: list[str] = []
    for body in bodies:
        for name in _keywords(body):
            if name not in allowed:
                unknown.append(name)
    assert not unknown, (
        "team_dispatch example keywords absent from the MCP signature: "
        + ", ".join(unknown)
    )
