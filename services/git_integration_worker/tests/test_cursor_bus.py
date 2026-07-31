"""CursorBusClient contract tests."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from services.git_integration_worker.cursor_bus import CursorBusClient


class _FakeResponse:
    def __init__(self, *, status_code: int, text: str) -> None:
        self.status_code = status_code
        self.text = text

    def json(self) -> dict[str, object]:
        raise ValueError("not json")


@pytest.mark.asyncio
async def test_latest_turn_number_treats_non_json_200_as_zero() -> None:
    client = CursorBusClient(token="test-token")
    http_client = AsyncMock()
    http_client.get = AsyncMock(
        return_value=_FakeResponse(status_code=200, text="<html>not json</html>")
    )

    result = await client._latest_turn_number(http_client, thread_id="1960")

    assert result == 0


@pytest.mark.asyncio
async def test_reply_survives_non_json_turns_list(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = CursorBusClient(token="test-token")

    class _Ctx:
        def __init__(self) -> None:
            self._client = AsyncMock()
            self._client.get = AsyncMock(
                return_value=_FakeResponse(status_code=200, text="not-json")
            )
            post_resp = MagicMock()
            post_resp.status_code = 201
            post_resp.json.return_value = {"turn_number": 2}
            post_resp.text = '{"turn_number": 2}'
            self._client.post = AsyncMock(return_value=post_resp)

        async def __aenter__(self) -> AsyncMock:
            return self._client

        async def __aexit__(self, *args: object) -> None:
            return None

    monkeypatch.setattr(
        "services.git_integration_worker.cursor_bus.make_async_client",
        lambda *a, **k: _Ctx(),
    )

    result = await client.reply(
        thread_id="1960",
        to_agent="claude-web",
        from_agent="cursor-sdk",
        subject="test",
        body="ok",
    )

    assert result.status_code == 201
