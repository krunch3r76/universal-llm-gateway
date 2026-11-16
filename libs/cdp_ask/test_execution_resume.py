"""Unit tests for execution resume seat identity (AC22)."""

from __future__ import annotations

import pytest

from cdp_ask.execution_resume import _cse_id

pytestmark = pytest.mark.offline


def test_resume_refuses_wrong_chat_tab_identity_mismatch() -> None:
    u1 = "https://claude.ai/chat/11111111-1111-4111-8111-111111111111"
    u2 = "https://claude.ai/chat/22222222-2222-4222-8222-222222222222"
    assert _cse_id(u1) != _cse_id(u2)
    c1 = "https://claude.ai/cowork/cse_alpha"
    c2 = "https://claude.ai/cowork/cse_beta"
    assert _cse_id(c1) != _cse_id(c2)