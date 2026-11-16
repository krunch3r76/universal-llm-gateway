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


def test_resume_chat_host_and_case_variants_match() -> None:
    uuid_lower = "2762063d-2966-4859-a3ce-23831c6e0c86"
    canonical = f"https://claude.ai/chat/{uuid_lower}"
    www_upper = f"https://www.claude.ai/chat/{uuid_lower.upper()}"
    assert _cse_id(canonical) == _cse_id(www_upper)
