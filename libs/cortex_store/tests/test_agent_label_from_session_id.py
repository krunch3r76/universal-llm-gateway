"""AC 1.10: agent slug strips dated tail from session_id."""

from __future__ import annotations

import pytest

from cortex_store.dispatch_ops.ops_transcript_seal import _agent_label_from_session_id

pytestmark = pytest.mark.offline


def test_web_anthropic_agent_label() -> None:
    assert (
        _agent_label_from_session_id("web-anthropic-2026-09-09-081640-a1b")
        == "web-anthropic"
    )
