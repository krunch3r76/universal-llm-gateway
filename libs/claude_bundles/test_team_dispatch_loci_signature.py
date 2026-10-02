"""Guard: team_dispatch( examples in named loci match the MCP signature.

Extends the operator-proxy hop guard to frontier.py descriptor prose and
skill/command loci from friction a:37288. Fails when examples use wire names
job= or session= as MCP keywords (Stargate maps contract→job, purpose→session).
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

pytestmark = pytest.mark.offline

_REPO = Path(__file__).resolve().parents[2]
_FRONTIER = _REPO / "services/mcp-server/tools/frontier.py"

# Named by closeout 58b874d14142 and handoff §3 — keep-list anything omitted here.
_LOCI_REL = (
    "services/mcp-server/tools/frontier.py",
    "cursor-plugins/ulg-ecosystem/skills/liaison/SKILL.md",
    "cursor-plugins/ulg-ecosystem/skills/liaison-cursor/SKILL.md",
    "cursor-plugins/ulg-ecosystem/skills/consult-routing/SKILL.md",
    "cursor-plugins/ulg-ecosystem/skills/consult-routing/routing-detail-annex.md",
    "cursor-plugins/ulg-ecosystem/skills/claude-ai-cdp-navigation/SKILL.md",
    "cursor-plugins/ulg-ecosystem/skills/claude-ai-cdp-navigation/reference-annex.md",
    "cursor-plugins/ulg-ecosystem/skills/checkpoint-discipline/SKILL.md",
    "cursor-plugins/ulg-ecosystem/skills/conductor/SKILL.md",
    "cursor-plugins/ulg-ecosystem/skills/conductor/reference-admit.md",
    "cursor-plugins/ulg-ecosystem/skills/conductor/reference-packet.md",
    "cursor-plugins/ulg-ecosystem/skills/conductor/reference-run-to-completion.md",
    "cursor-plugins/ulg-ecosystem/skills/cdp-operator-proxy/SKILL.md",
    "cursor-plugins/ulg-ecosystem/commands/conductor.md",
    "scripts/model_manager/ui/controller/charter_runner/tick_sos.py",
    "pipelines/operator_hop_harvest/v1/handlers/assemble.py",
)

# Substrings in frontier.py that must not read as MCP team_dispatch call syntax.
_FRONTIER_BAD_DESCRIPTOR_FRAGMENTS = (
    "An ad-hoc edit uses `job=freeform`",
    "CHECKPOINT tip: `seat=cursor-sdk`, `model=cursor/grok-4.7`, `job=freeform`",
    "Set session=operator-proxy, job=freeform or mission",
)


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
            raise AssertionError("unclosed team_dispatch( in scanned text")
        bodies.append(text[index + len(needle) : cursor - 1])
        start = cursor
    return bodies


def _keywords(body: str) -> list[str]:
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


def test_loci_team_dispatch_keywords_match_mcp_signature() -> None:
    allowed = _mcp_team_dispatch_param_names()
    assert allowed, "frontier.py team_dispatch signature was empty"
    unknown: list[str] = []
    for rel in _LOCI_REL:
        path = _REPO / rel
        assert path.is_file(), f"missing locus file {rel}"
        for body in _call_bodies(path.read_text(encoding="utf-8")):
            for name in _keywords(body):
                if name not in allowed:
                    unknown.append(f"{rel}: {name}")
    assert not unknown, (
        "team_dispatch example keywords absent from the MCP signature: "
        + ", ".join(sorted(set(unknown)))
    )


def test_frontier_team_dispatch_descriptor_uses_contract_and_purpose() -> None:
    text = _FRONTIER.read_text(encoding="utf-8")
    hits = [frag for frag in _FRONTIER_BAD_DESCRIPTOR_FRAGMENTS if frag in text]
    assert not hits, (
        "frontier.py team_dispatch descriptor still uses job=/session= as MCP call syntax: "
        + "; ".join(hits)
    )
