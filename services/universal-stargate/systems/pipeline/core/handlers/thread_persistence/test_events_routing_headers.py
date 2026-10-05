"""cx_async internal routing headers."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from systems.pipeline.core.handlers.thread_persistence.events import cx_async

pytestmark = pytest.mark.offline


@pytest.mark.asyncio
async def test_cx_async_sends_internal_routing_headers() -> None:
    from unittest.mock import MagicMock

    response = MagicMock()
    response.status_code = 200
    response.json.return_value = {"ok": True}

    client = MagicMock()
    client.post = AsyncMock(return_value=response)
    client_cm = MagicMock()
    client_cm.__aenter__ = AsyncMock(return_value=client)
    client_cm.__aexit__ = AsyncMock(return_value=None)

    with patch(
        "systems.pipeline.core.handlers.thread_persistence.events.make_async_client",
        return_value=client_cm,
    ):
        await cx_async("stats", {})

    kwargs = client.post.call_args.kwargs
    assert kwargs["headers"]["X-ULG-Caller"] == "thread_persistence"
    assert "via_adapter" not in kwargs["json"]
