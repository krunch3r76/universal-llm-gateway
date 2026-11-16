"""Unit tests for dormant drain CSE URL probe (AC21)."""

from __future__ import annotations

from unittest.mock import patch

import pytest

pytestmark = pytest.mark.offline


def test_probe_cse_url_accepts_unified_chat() -> None:
    uuid = "2762063d-2966-4859-a3ce-23831c6e0c86"
    chat = f"https://claude.ai/chat/{uuid}"
    from claude_bundles.cdp_registry.dormant_drain import _probe_cse_url

    with patch(
        "claude_bundles.cdp_registry.session_address._default_probe_page_urls",
        return_value=[chat, "https://claude.ai/new"],
    ):
        assert _probe_cse_url(9222) == chat