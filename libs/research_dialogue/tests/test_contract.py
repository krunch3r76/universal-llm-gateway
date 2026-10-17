from __future__ import annotations

import pytest

from research_dialogue.contract import parse_contract

pytestmark = pytest.mark.offline


def test_parse_ok() -> None:
    text = """
<answer> reasoning </answer>
<ledger>F-a | x | high | rs-1 | f </ledger>
<next_queries>
knowledge_systems | decay
</next_queries>
<status> continue </status>
"""
    p = parse_contract(text)
    assert p.ok
    assert p.next_queries == [("knowledge_systems", "decay")]


def test_extra_queries_flagged() -> None:
    lines = "\n".join(f"scope | q{i}" for i in range(6))
    text = f"<answer>a</answer><ledger>l</ledger><next_queries>{lines}</next_queries><status>continue</status>"
    p = parse_contract(text)
    assert p.ok
    assert p.extra_query_lines == 2
    assert len(p.next_queries) == 4


def test_malformed_missing_tag() -> None:
    p = parse_contract("<answer>x</answer>")
    assert not p.ok
