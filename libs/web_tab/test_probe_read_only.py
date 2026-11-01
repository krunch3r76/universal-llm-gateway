"""AC2: probe is read-only and target-scoped."""

from __future__ import annotations

import pytest

from web_tab.cdp_port import RecordingCdpPort
from web_tab.probe import probe_target


@pytest.mark.asyncio
async def test_probe_emits_off_the_event_loop(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ingest runs in a worker thread so the socket wait cannot block the loop."""
    scheduled: list[str] = []

    async def _to_thread(fn, /, *args, **kwargs):  # noqa: ANN001
        scheduled.append(getattr(fn, "__name__", ""))
        return fn(*args, **kwargs)

    monkeypatch.setattr("web_tab.probe.asyncio.to_thread", _to_thread)
    port = RecordingCdpPort(
        tabs=[{"id": "a", "url": "https://chatgpt.com/c/1", "title": ""}],
        evaluate_results={"a": {"signed_in": True}},
    )
    result = await probe_target(
        cdp_url="http://127.0.0.1:9342", target_id="a", port=port
    )
    assert result["ok"] is True
    assert "emit_probe_completed" in scheduled
    assert scheduled[-1] == "emit_probe_completed"


@pytest.mark.asyncio
async def test_probe_never_navigates_or_clicks() -> None:
    port = RecordingCdpPort(
        tabs=[{"id": "a", "url": "https://chatgpt.com/c/1", "title": ""}],
        evaluate_results={"a": {"signed_in": True}},
    )
    result = await probe_target(
        cdp_url="http://127.0.0.1:9342", target_id="a", port=port
    )
    assert result["read_only"] is True
    assert port.navigated == []
    assert port.clicks == []


@pytest.mark.asyncio
async def test_probe_scoped_to_target() -> None:
    port = RecordingCdpPort(
        tabs=[
            {"id": "a", "url": "https://chatgpt.com/c/one", "title": "A"},
            {"id": "b", "url": "https://chatgpt.com/c/two", "title": "B"},
        ],
        evaluate_results={
            "a": {"signed_in": True},
            "b": {"signed_in": False},
        },
    )
    one = await probe_target(cdp_url="http://127.0.0.1:9342", target_id="a", port=port)
    two = await probe_target(cdp_url="http://127.0.0.1:9342", target_id="b", port=port)
    assert one["actuation"]["signed_in"]["signed_in"] is True
    assert two["actuation"]["signed_in"]["signed_in"] is False


@pytest.mark.asyncio
async def test_probe_target_closed_or_changed() -> None:
    port = RecordingCdpPort(
        tabs=[{"id": "other", "url": "https://chatgpt.com/", "title": ""}]
    )
    result = await probe_target(
        cdp_url="http://127.0.0.1:9342", target_id="missing", port=port
    )
    assert result["actuation"]["signed_in"].get("stale") is True
    assert result["actuation"]["signed_in"]["signed_in"] is None


@pytest.mark.asyncio
async def test_two_conversations_one_profile() -> None:
    """Two tabs in one profile stay distinct by target_id."""
    port = RecordingCdpPort(
        tabs=[
            {"id": "main", "url": "https://chatgpt.com/c/main-id", "title": "21207"},
            {"id": "sat", "url": "https://chatgpt.com/c/sat-id", "title": "21342"},
        ],
        evaluate_results={"main": {"signed_in": True}, "sat": {"signed_in": True}},
    )
    main = await probe_target(
        cdp_url="http://127.0.0.1:9342", target_id="main", port=port
    )
    sat = await probe_target(
        cdp_url="http://127.0.0.1:9342", target_id="sat", port=port
    )
    assert main["actuation"]["tabs"][0]["conversation_id"] == "main-id"
    assert sat["actuation"]["tabs"][0]["conversation_id"] == "sat-id"


@pytest.mark.asyncio
async def test_unsupported_surface_structured_error() -> None:
    result = await probe_target(
        cdp_url="http://127.0.0.1:9342", surface="unknown-ui", target_id="a"
    )
    assert result["ok"] is False
    assert result["error"]["code"] == "unsupported_surface"
