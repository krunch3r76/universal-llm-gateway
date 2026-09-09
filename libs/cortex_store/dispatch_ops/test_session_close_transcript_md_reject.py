"""MCP/dispatch session_close rejects removed transcript_md (G6 P1.1)."""

from __future__ import annotations

import pytest

from cortex_store.dispatch_ops import execute_op

pytestmark = pytest.mark.offline

_MINIMAL = {
    "session_id": "cursor-2026-09-09-120000-a01",
    "agent": "cursor",
    "session_summary_md": "## Session Summary\n\n**Decisions:** x\n",
    "summary": "Reject removed transcript_md on MCP dispatch path.",
    "transcript_md": "# Transcript\n\n## Turn 1 — hi\n",
}


@pytest.mark.parametrize(
    "tool",
    ["session_close", "session_close_preflight"],
)
def test_execute_op_rejects_transcript_md_with_422(tool: str) -> None:
    result = execute_op(tool, dict(_MINIMAL))
    assert result.get("status_code") == 422
    assert result.get("reason") == "transcript_md.removed"
    assert result.get("field") == "transcript_md"
