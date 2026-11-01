"""Production CDP port: target id plus canonical URL, no second list, no browser close."""

from __future__ import annotations

import pytest

from web_tab.cdp_port import HttpListCdpPort, RecordingCdpPort
from web_tab.observation import observe_actuation_plane


class _Session:
    def __init__(self, target_id: str) -> None:
        self._target_id = target_id

    async def send(self, method: str) -> dict:
        assert method == "Target.getTargetInfo"
        return {"targetInfo": {"targetId": self._target_id}}


class _Page:
    def __init__(self, url: str, target_id: str) -> None:
        self.url = url
        self.target_id = target_id
        self.evals = 0
        self.context = self

    async def evaluate(self, expression: str) -> dict:  # noqa: ARG002
        self.evals += 1
        return {
            "signed_in": True,
            "url": self.url,
            "login_wall": False,
            "composer": True,
        }

    async def new_cdp_session(self, page: object) -> _Session:  # noqa: ARG002
        return _Session(self.target_id)


class _Context:
    def __init__(self, pages: list[_Page]) -> None:
        self.pages = pages


class _Browser:
    def __init__(self, pages: list[_Page]) -> None:
        self.contexts = [_Context(pages)]
        self.closed = False

    async def close(self) -> None:
        self.closed = True


def _port(browser: _Browser) -> HttpListCdpPort:
    async def _connect(cdp_url: str) -> _Browser:  # noqa: ARG001
        return browser

    port = HttpListCdpPort(connect=_connect)

    def _list_tabs(*, cdp_url: str) -> dict:  # noqa: ARG001
        raise AssertionError("evaluate must not list tabs again")

    port.list_tabs = _list_tabs  # type: ignore[method-assign]
    return port


@pytest.mark.asyncio
async def test_same_url_twins_attach_only_matching_target() -> None:
    url = "https://chatgpt.com/c/same"
    first = _Page(url, "target-a")
    second = _Page(url + "/", "target-b")
    browser = _Browser([first, second])
    raw = await _port(browser).evaluate_on_target(
        cdp_url="http://127.0.0.1:9342",
        target_id="target-b",
        expression="() => ({})",
        tab_row={"id": "target-b", "url": url},
    )
    assert raw["signed_in"] is True
    assert first.evals == 0
    assert second.evals == 1
    assert browser.closed is False


@pytest.mark.asyncio
async def test_target_change_between_list_and_attach_is_stale() -> None:
    page = _Page("https://chatgpt.com/c/same", "target-now")
    browser = _Browser([page])
    raw = await _port(browser).evaluate_on_target(
        cdp_url="http://127.0.0.1:9342",
        target_id="target-listed",
        expression="() => ({})",
        tab_row={"id": "target-listed", "url": "https://chatgpt.com/c/same"},
    )
    assert raw.get("stale") is True
    assert page.evals == 0
    assert browser.closed is False


@pytest.mark.asyncio
async def test_out_of_scope_host_does_not_connect() -> None:
    def _connect(cdp_url: str):  # noqa: ARG001
        raise AssertionError("connect")

    port = HttpListCdpPort(connect=_connect)  # type: ignore[arg-type]
    raw = await port.evaluate_on_target(
        cdp_url="http://127.0.0.1:9342",
        target_id="g",
        expression="() => ({})",
        tab_row={"id": "g", "url": "https://accounts.google.com/AccountChooser"},
    )
    assert raw["error"]["code"] == "target_out_of_scope"


@pytest.mark.asyncio
async def test_claude_surface_allows_claude_ai_tab() -> None:
    page = _Page("https://claude.ai/", "claude-tab")
    browser = _Browser([page])
    raw = await _port(browser).evaluate_on_target(
        cdp_url="http://127.0.0.1:9343",
        target_id="claude-tab",
        expression="() => ({})",
        tab_row={"id": "claude-tab", "url": "https://claude.ai/"},
        surface="claude",
    )
    assert raw["signed_in"] is True
    assert page.evals == 1
    assert browser.closed is False


@pytest.mark.asyncio
async def test_recording_port_refuses_wrong_surface_before_evaluate() -> None:
    port = RecordingCdpPort(
        tabs=[{"id": "g", "url": "https://accounts.google.com/x", "title": "g"}],
        evaluate_results={"g": {"signed_in": True}},
    )
    plane = await observe_actuation_plane(
        cdp_url="http://127.0.0.1:9342",
        target_id="g",
        port=port,
        probe_js="() => ({})",
        surface="chatgpt",
    )
    assert plane["error"]["code"] == "target_out_of_scope"
    assert port.evaluated_targets == []
    assert plane["signed_in"]["signed_in"] is None


@pytest.mark.asyncio
async def test_claude_wrong_port_and_scheme_refused_without_connect() -> None:
    def _connect(cdp_url: str):  # noqa: ARG001
        raise AssertionError("connect")

    port = HttpListCdpPort(connect=_connect)  # type: ignore[arg-type]
    for tab_url in ("https://claude.ai:8080/", "http://claude.ai/"):
        raw = await port.evaluate_on_target(
            cdp_url="http://127.0.0.1:9343",
            target_id="t",
            expression="() => ({})",
            tab_row={"id": "t", "url": tab_url},
            surface="claude",
        )
        assert raw["error"]["code"] == "target_out_of_scope"
        assert "url" not in raw


@pytest.mark.asyncio
async def test_observe_actuation_out_of_scope_clears_url_epoch() -> None:
    port = RecordingCdpPort(
        tabs=[
            {
                "id": "bad",
                "url": "https://claude.ai:8080/chat?q=secret",
                "title": "x",
            }
        ],
    )

    plane = await observe_actuation_plane(
        cdp_url="http://127.0.0.1:9343",
        target_id="bad",
        port=port,
        probe_js="() => ({})",
        surface="claude",
    )
    assert plane["error"]["code"] == "target_out_of_scope"
    epoch = plane["signed_in"]["epoch"]
    assert epoch.get("url") == ""
    assert epoch.get("conversation_id") is None
    assert port.evaluated_targets == []


@pytest.mark.asyncio
async def test_observe_actuation_http_tab_out_of_scope_without_connect() -> None:
    """Cleartext tab URLs are filtered before CDP attach (same as evaluate path)."""
    port = RecordingCdpPort(
        tabs=[{"id": "http-tab", "url": "http://claude.ai/", "title": "cleartext"}],
        evaluate_results={"http-tab": {"signed_in": True}},
    )
    plane = await observe_actuation_plane(
        cdp_url="http://127.0.0.1:9343",
        target_id="http-tab",
        port=port,
        probe_js="() => ({})",
        surface="claude",
    )
    assert plane["error"]["code"] == "target_out_of_scope"
    assert port.evaluated_targets == []


@pytest.mark.asyncio
async def test_malformed_bracket_tab_in_list_does_not_fail_probe() -> None:
    port = RecordingCdpPort(
        tabs=[
            {"id": "ok", "url": "https://claude.ai/", "title": "ok"},
            {"id": "garbage", "url": "https://[claude.ai/", "title": "bad"},
        ],
        evaluate_results={"ok": {"signed_in": True}},
    )
    plane = await observe_actuation_plane(
        cdp_url="http://127.0.0.1:9343",
        target_id="ok",
        port=port,
        probe_js="() => ({})",
        surface="claude",
    )
    assert plane["signed_in"]["signed_in"] is True
    assert port.evaluated_targets == ["ok"]


@pytest.mark.asyncio
async def test_malformed_port_tab_in_list_does_not_fail_probe() -> None:
    port = RecordingCdpPort(
        tabs=[
            {"id": "ok", "url": "https://claude.ai/", "title": "ok"},
            {"id": "garbage", "url": "https://claude.ai:abc/", "title": "bad"},
        ],
        evaluate_results={"ok": {"signed_in": True}},
    )
    plane = await observe_actuation_plane(
        cdp_url="http://127.0.0.1:9343",
        target_id="ok",
        port=port,
        probe_js="() => ({})",
        surface="claude",
    )
    assert plane["signed_in"]["signed_in"] is True
    assert port.evaluated_targets == ["ok"]
