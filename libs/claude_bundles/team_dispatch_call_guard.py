"""Parse MCP tool call examples and compare keywords to live signatures."""

from __future__ import annotations

import ast
import re
from pathlib import Path

_CALL_START = re.compile(r"(?<![\w.])team_dispatch\(")
_BACKTICK_JOB_SESSION = re.compile(r"`(?:job|session)=")


def mcp_tool_param_names(frontier_path: Path, tool_name: str) -> set[str]:
    tree = ast.parse(frontier_path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == tool_name:
            names = {arg.arg for arg in node.args.args}
            names.update(arg.arg for arg in node.args.kwonlyargs)
            return names
    raise AssertionError(f"{tool_name} def not found in {frontier_path}")


def call_bodies(text: str, *, needle: str = "team_dispatch(") -> list[str]:
    if needle != "team_dispatch(":
        pattern = re.compile(re.escape(needle))
    else:
        pattern = _CALL_START

    bodies: list[str] = []
    start = 0
    while True:
        match = pattern.search(text, start)
        if not match:
            break
        cursor = match.end()
        depth = 1
        while cursor < len(text) and depth:
            char = text[cursor]
            if char == "(":
                depth += 1
            elif char == ")":
                depth -= 1
            cursor += 1
        if depth:
            raise AssertionError(f"unclosed {needle} in scanned text")
        bodies.append(text[match.end() : cursor - 1])
        start = cursor
    return bodies


def _strip_trailing_comment(line: str) -> str:
    in_string: str | None = None
    escape = False
    for i, char in enumerate(line):
        if in_string is not None:
            if escape:
                escape = False
            elif char == "\\":
                escape = True
            elif char == in_string:
                in_string = None
            continue
        if char in {'"', "'"}:
            in_string = char
            continue
        if char == "#":
            return line[:i]
    return line


def call_example_keywords(body: str) -> list[str]:
    """Top-level name= keywords; ignores strings, braces, angle brackets, comments."""
    names: list[str] = []
    depth = 0
    angle_depth = 0
    in_string: str | None = None
    escape = False
    token: list[str] = []
    normalized = "\n".join(_strip_trailing_comment(line) for line in body.splitlines())
    for char in normalized:
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
        if char == "<":
            angle_depth += 1
            token = []
            continue
        if char == ">":
            angle_depth = max(0, angle_depth - 1)
            token = []
            continue
        if depth == 0 and angle_depth == 0 and char == "=" and token:
            name = "".join(token).strip()
            if name.isidentifier():
                names.append(name)
            token = []
            continue
        if char.isalnum() or char == "_":
            token.append(char)
        else:
            token = []
    if in_string is not None:
        raise AssertionError(f"unclosed string in call example: {in_string!r}")
    return names


def frontier_descriptor_bad_mcp_call_syntax(frontier_text: str) -> list[str]:
    """Backticked `job=` / `session=` inside team_dispatch docstring or purpose= Field text."""
    tree = ast.parse(frontier_text)
    doc = ""
    purpose_desc = ""
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "team_dispatch":
            doc = ast.get_docstring(node) or ""
            break
    purpose_match = re.search(
        r'purpose:\s*Annotated[\s\S]*?description=\(\s*"([^"]*(?:\\.[^"]*)*)"',
        frontier_text,
    )
    if purpose_match:
        purpose_desc = purpose_match.group(1).encode().decode("unicode_escape")
    hits: list[str] = []
    for label, blob in (("docstring", doc), ("purpose_field", purpose_desc)):
        for m in _BACKTICK_JOB_SESSION.finditer(blob):
            hits.append(f"{label}: {m.group(0)}")
    return hits
