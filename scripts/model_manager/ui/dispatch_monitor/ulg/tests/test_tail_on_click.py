"""TailOnClick returns a cursor tail and never raises."""

from __future__ import annotations

from scripts.model_manager.ui.dispatch_monitor.ulg.tail_on_click import TailOnClick


def test_sdk_tail_passes_through_lines_after_cursor() -> None:
    def sdk_fetch(key: str, cursor: int) -> dict:
        assert key == "disp-1"
        assert cursor == 1
        return {
            "lines": [{"index": 2, "kind": "thinking", "text": "next"}],
            "cursor": 2,
            "eof": False,
            "source": "sdk.run_lines",
        }

    port = TailOnClick(sdk_fetch=sdk_fetch, cdp_fetch=lambda *_a: {})
    body = port.tail("cursor-sdk", "disp-1", 1)
    assert body["lines"][0]["text"] == "next"
    assert body["cursor"] == 2
    assert body["eof"] is False
    assert body["source"] == "sdk.run_lines"


def test_cdp_tail_maps_completed_turns() -> None:
    def cdp_fetch(key: str, cursor: int) -> dict:
        assert key == "reg-9"
        assert cursor == 3
        return {
            "outcome": "ok",
            "cursor": 5,
            "turns": [
                {"author": "assistant", "text": "done", "ordinal": 4},
                {"author": "assistant", "text": "   ", "ordinal": 5},
            ],
        }

    port = TailOnClick(sdk_fetch=lambda *_a: {}, cdp_fetch=cdp_fetch)
    body = port.tail("cdp", "reg-9", 3)
    assert body["source"] == "cse-dom"
    assert body["eof"] is True
    assert body["cursor"] == 5
    assert body["lines"] == [
        {"index": 4, "kind": "assistant", "text": "done"},
    ]


def test_cdp_streaming_is_not_eof() -> None:
    port = TailOnClick(
        sdk_fetch=lambda *_a: {},
        cdp_fetch=lambda *_a: {"outcome": "streaming", "turns": []},
    )
    body = port.tail("cse", "https://claude.ai/chat/1", 0)
    assert body["eof"] is False
    assert body["source"] == "cse-dom"


def test_unsupported_kind_and_fetch_errors_do_not_raise() -> None:
    port = TailOnClick(
        sdk_fetch=lambda *_a: (_ for _ in ()).throw(RuntimeError("down")),
        cdp_fetch=lambda *_a: {},
    )
    bad = port.tail("prompt", "x", 2)
    assert bad["error"] == "unsupported_kind"
    assert bad["cursor"] == 2
    failed = port.tail("sdk", "x", 2)
    assert failed["error"] == "RuntimeError"
    assert failed["lines"] == []
    assert failed["eof"] is True
