"""Drift gate: canonical.yaml team_dispatch contract enums vs code vocabulary."""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from team_dispatch_vocab import TEAM_DISPATCH_CONTRACTS, TO_THREAD_CONTRACTS

REPO = Path(__file__).resolve().parent.parent
CANONICAL = REPO / "config/mcp/canonical.yaml"


def _contract_enums_for_tool(text: str, tool_name: str) -> frozenset[str]:
    block_match = re.search(
        rf"- canonical_name: {re.escape(tool_name)}\b[\s\S]*?json_schema:[\s\S]*?"
        r"contract:\n        type: string\n        enum:\n((?:        - .+\n)+)",
        text,
    )
    assert block_match is not None, f"missing contract enum for {tool_name}"
    values = re.findall(r"        - (\S+)", block_match.group(1))
    return frozenset(values)


@pytest.mark.offline
def test_canonical_to_thread_contract_enum_matches_vocab() -> None:
    text = CANONICAL.read_text(encoding="utf-8")
    for tool in ("team_dispatch_generate", "team_dispatch_to_thread"):
        assert _contract_enums_for_tool(text, tool) == TO_THREAD_CONTRACTS


@pytest.mark.offline
def test_team_dispatch_contracts_cover_materializer_and_residual() -> None:
    assert TEAM_DISPATCH_CONTRACTS >= {"sketch", "implement", "wrap", "conductor", "none"}
