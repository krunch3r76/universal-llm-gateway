"""Canonical URL matching for ``find_page_on_lane``."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from cdp_ask.followup_reattach import find_page_on_lane

pytestmark = pytest.mark.offline

_CHAT = "https://claude.ai/chat/2762063d-2966-4859-a3ce-23831c6e0c86"
_WWW = (
    "https://www.claude.ai/chat/"
    "2762063D-2966-4859-A3CE-23831C6E0C86"
)


@pytest.mark.asyncio
async def test_find_page_on_lane_matches_www_page_spelling(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    page = MagicMock()
    page.url = _WWW
    ctx = MagicMock()
    ctx.pages = [page]
    pw = MagicMock()
    pw.stop = AsyncMock()

    async def _connect(_cdp: str) -> tuple[MagicMock, MagicMock, MagicMock, MagicMock]:
        return pw, MagicMock(), ctx, page

    monkeypatch.setattr("cdp_ask.followup_reattach.connect_cdp", _connect)
    found = await find_page_on_lane("http://127.0.0.1:9223", _CHAT)
    assert found is not None
    assert found[0] is page
