"""Drift gate: canonical.yaml + live descriptor vs job_vocab admit sets."""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from job_vocab import GENERATE_ADMITTED_JOBS, TO_THREAD_ADMITTED_JOBS

REPO = Path(__file__).resolve().parent.parent
CANONICAL = REPO / "config/mcp/canonical.yaml"
FRONTIER = REPO / "services/mcp-server/tools/frontier.py"


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
def test_canonical_generate_contract_enum_matches_job_vocab() -> None:
    text = CANONICAL.read_text(encoding="utf-8")
    assert _contract_enums_for_tool(text, "team_dispatch_generate") == (
        GENERATE_ADMITTED_JOBS
    )


@pytest.mark.offline
def test_canonical_to_thread_contract_enum_matches_job_vocab() -> None:
    text = CANONICAL.read_text(encoding="utf-8")
    assert _contract_enums_for_tool(text, "team_dispatch_to_thread") == (
        TO_THREAD_ADMITTED_JOBS
    )


@pytest.mark.offline
def test_frontier_description_interpolates_job_vocab() -> None:
    """Prose shell must not re-list retired none/pure-mechanical tokens."""
    source = FRONTIER.read_text(encoding="utf-8")
    assert "GENERATE_JOBS" in source
    assert "TO_THREAD_JOBS" in source
    assert "_format_team_dispatch_description" in source
    # Retired tokens must not appear as taught generate contracts in the template.
    assert "`none`" not in source
    assert "`pure-mechanical`" not in source
    assert "Allowed on every admitted " in source
    assert "generate job." in source
    assert "including none" not in source
