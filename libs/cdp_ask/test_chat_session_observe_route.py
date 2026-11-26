"""Route tests for POST /v1/chat-session/observe."""

from __future__ import annotations

import hashlib
import inspect
import json
import re
from pathlib import Path
from typing import Any

import pytest
from chat_harvest.models import ChatHarvestRequest, ChatHarvestResponse
from fastapi.testclient import TestClient

from cdp_ask.app import create_app
from cdp_ask.chat_session_harvest import execute_probe

pytestmark = pytest.mark.offline

CLAUDE_CHAT_URL = "https://claude.ai/chat/thread-uuid"
GROK_URL = "https://grok.com/c/47794c69-9fcc-4481-b1a6-f6c9cbf8b768"
CSE_URL = "https://claude.ai/cowork/cse_abc123"
PROBE_ENDPOINT_SHA = (
    "0b478d15a4b71f28d319fc398deb69210827d0e1f623a91cf06ffe58d5ebe006"
)
EXECUTE_PROBE_SHA = "221aad3b0415742d64afb117470ef6b6c78b673cd5b769baf1aa59ad10b30bfc"

_HARVEST_FIELDS = sorted(ChatHarvestRequest.model_fields.keys())
_RESPONSE_FIELDS = sorted(ChatHarvestResponse.model_fields.keys())


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("CORTEX_FILES_ROOT", str(tmp_path))
    monkeypatch.setattr(
        "claude_bundles.cdp_orphans.probe_live_ports",
        lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("startup must not probe")),
    )
    monkeypatch.setattr("cdp_ask.app.verify_harvest_root", lambda: None)
    return TestClient(create_app())


@pytest.fixture
def observed_events(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    captured: list[dict[str, Any]] = []

    def _capture(event: object) -> None:
        captured.append({"signal": event.signal, "payload": dict(event.payload)})

    monkeypatch.setattr("cdp_ask.chat_session_observe.emit", _capture)
    return captured


def _install_cdp_fake(monkeypatch: pytest.MonkeyPatch, *, collect: dict[str, Any]) -> list[dict[str, Any]]:
    from chat_harvest.readonly_cdp import set_open_connection
    from chat_harvest.test_readonly_cdp import FakeSocket

    recorded: list[dict[str, Any]] = []
    target = {
        "type": "page",
        "id": "p1",
        "url": CLAUDE_CHAT_URL,
        "webSocketDebuggerUrl": "ws://127.0.0.1:9222/devtools/page/p1",
    }

    def _http_get(_cdp_self: Any, url: str) -> Any:
        recorded.append({"http": url})
        if url.endswith("/json/list"):
            return [target]
        return {"webSocketDebuggerUrl": "ws://127.0.0.1:9222/devtools/browser/x"}

    monkeypatch.setattr(
        "chat_harvest.readonly_cdp.ReadonlyCDP.http_get_json",
        _http_get,
    )

    call = {"n": 0}

    def _open(*_a: Any, **_k: Any) -> FakeSocket:
        call["n"] += 1
        if call["n"] <= 6:
            return FakeSocket(
                evaluate_value={
                    "turn_nodes": 2,
                    "element_count": 10,
                    "login_wall": False,
                    "visibility_state": "visible",
                }
            )
        return FakeSocket(evaluate_value=collect)

    set_open_connection(_open)
    monkeypatch.setattr("chat_harvest.dom_observation.time.sleep", lambda _s: None)
    return recorded


def test_observe_200_real_handler_fake_transport(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    long_text = ("token " * 400).strip()
    collect_payload = {
        "nodes": [
            {
                "index": 0,
                "region": "outside",
                "tag": "div",
                "text": {"page_text": long_text, "chars": 2000, "cut": False},
                "controls": [],
                "position": {"rect": [0, 0, 1, 1], "in_viewport": True, "visible": True},
                "path": ["main"],
            }
        ],
        "counts": {"total": 1, "outside": 1, "returned": 1},
        "page": {"title": {"page_text": "t", "chars": 1, "cut": False}},
    }

    from chat_harvest.readonly_cdp import set_open_connection
    from chat_harvest.test_readonly_cdp import FakeSocket

    target = {
        "type": "page",
        "id": "p1",
        "url": CLAUDE_CHAT_URL,
        "webSocketDebuggerUrl": "ws://127.0.0.1:9222/devtools/page/p1",
    }

    monkeypatch.setattr(
        "chat_harvest.readonly_cdp.ReadonlyCDP.http_get_json",
        lambda _self, url: (
            [target] if url.endswith("/json/list") else {"webSocketDebuggerUrl": "ws://b"}
        ),
    )
    ready = {
        "turn_nodes": 2,
        "element_count": 10,
        "login_wall": False,
        "visibility_state": "visible",
    }

    class SeqSocket(FakeSocket):
        def recv(self) -> str:
            value = collect_payload if len(self.frames) >= 5 else ready
            msg_id = self.frames[-1]["id"]
            return json.dumps({"id": msg_id, "result": {"result": {"value": value}}})

    set_open_connection(lambda *_a, **_k: SeqSocket())
    monkeypatch.setattr("chat_harvest.dom_observation.time.sleep", lambda _s: None)
    resp = client.post(
        "/v1/chat-session/observe",
        json={"url": CLAUDE_CHAT_URL, "match_terms": ["paused", "card"]},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body.get("outcome") == "observed", body
    assert body["observation"]["looked"] is True, body
    node = body["observation"]["nodes"][0]
    assert node["text"]["chars"] == 2000
    assert node["text"]["cut"] is True
    assert len(node["text"]["page_text"]) == 500
    assert "cdp_methods" in body["observation"]
    assert body["observation"]["counts"]["total"] == 1


def test_observe_emits_one_event_without_page_text(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    observed_events: list[dict[str, Any]],
) -> None:
    collect = {
        "nodes": [{"text": {"page_text": "SECRET_PAGE_TEXT", "chars": 16, "cut": False}, "controls": []}],
        "counts": {"total": 1},
        "page": {},
    }
    _install_cdp_fake(monkeypatch, collect=collect)
    client.post(
        "/v1/chat-session/observe",
        json={"url": CLAUDE_CHAT_URL, "match_terms": ["sec", "ret"]},
    )
    assert len(observed_events) == 1
    assert observed_events[0]["signal"] == "mcp.chat.session.observed"
    assert "SECRET_PAGE_TEXT" not in str(observed_events[0]["payload"])
    assert "opened_on_demand" not in observed_events[0]["payload"]


def test_observe_409_emits_no_event(
    client: TestClient, observed_events: list[dict[str, Any]]
) -> None:
    resp = client.post(
        "/v1/chat-session/observe",
        json={"url": CLAUDE_CHAT_URL, "match_terms": []},
    )
    assert resp.status_code == 409
    assert observed_events == []


@pytest.mark.parametrize(
    "payload",
    [
        {"url": CLAUDE_CHAT_URL, "match_terms": []},
        {"url": CLAUDE_CHAT_URL, "match_terms": [f"t{i}" for i in range(9)]},
        {"url": CLAUDE_CHAT_URL, "match_terms": ["x"]},
        {"url": CLAUDE_CHAT_URL, "match_terms": ["a" * 65]},
        {"url": CLAUDE_CHAT_URL, "match_terms": ["ok", "term"], "max_nodes": 0},
        {"url": CLAUDE_CHAT_URL, "match_terms": ["ok", "term"], "max_nodes": 51},
    ],
)
def test_invalid_pattern_409_zero_browser_calls(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    payload: dict[str, Any],
) -> None:
    recorded = _install_cdp_fake(monkeypatch, collect={"nodes": [], "counts": {"total": 0}, "page": {}})
    resp = client.post("/v1/chat-session/observe", json=payload)
    assert resp.status_code == 409
    body = resp.json()
    assert body["code"] == "invalid_pattern"
    assert body["source"] == "gateway"
    assert body["retryable"] is False
    assert "field" in body["data"]
    http_calls = [r for r in recorded if "http" in r]
    assert http_calls == []


def test_cdp_url_outside_loopback_fleet_ports_409(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    recorded = _install_cdp_fake(monkeypatch, collect={})
    resp = client.post(
        "/v1/chat-session/observe",
        json={
            "url": CLAUDE_CHAT_URL,
            "match_terms": ["ok", "term"],
            "cdp_url": "http://example.com:9222",
        },
    )
    assert resp.status_code == 409
    assert resp.json()["code"] == "invalid_cdp_url"
    assert recorded == []


def test_cowork_url_409_use_cse_session(client: TestClient) -> None:
    resp = client.post(
        "/v1/chat-session/observe",
        json={"url": CSE_URL, "match_terms": ["a", "b"]},
    )
    assert resp.status_code == 409
    assert resp.json()["code"] == "use_cse_session"


def test_grok_url_refused_unsupported_site(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    recorded = _install_cdp_fake(monkeypatch, collect={})
    resp = client.post(
        "/v1/chat-session/observe",
        json={"url": GROK_URL, "match_terms": ["a", "b"]},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["outcome"] == "refused"
    assert body["code"] == "unsupported_site"
    assert recorded == []


def test_probe_route_and_execute_probe_source_frozen() -> None:
    from cdp_ask import app as app_module

    app_src = Path(app_module.__file__).read_text(encoding="utf-8")
    m = re.search(
        r'(@app\.post\("/v1/chat-session/probe"\).*?)(?=\n    @app\.post)',
        app_src,
        re.S,
    )
    assert m is not None
    assert hashlib.sha256(m.group(1).strip().encode()).hexdigest() == PROBE_ENDPOINT_SHA
    probe_src = inspect.getsource(execute_probe)
    assert hashlib.sha256(probe_src.encode()).hexdigest() == EXECUTE_PROBE_SHA


def test_probe_never_enters_observe_path(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _boom(**_kwargs: object) -> None:
        raise AssertionError("observe path invoked")

    monkeypatch.setattr("cdp_ask.chat_session_observe.execute_observe", _boom)

    async def _fake_harvest(**_kwargs: object) -> ChatHarvestResponse:
        return ChatHarvestResponse(
            outcome="harvested",
            site="claude",
            conversation_id="thread-uuid",
            url=CLAUDE_CHAT_URL,
            turn_count=1,
        )

    monkeypatch.setattr(
        "cdp_ask.chat_session_harvest.execute_claude_harvest",
        _fake_harvest,
    )
    monkeypatch.setattr(
        "cdp_ask.chat_session_harvest.execute_grok_harvest",
        _fake_harvest,
    )

    for url in (CLAUDE_CHAT_URL, GROK_URL):
        resp = client.post("/v1/chat-session/probe", json={"url": url})
        assert resp.status_code == 200


def test_probe_models_field_names_frozen() -> None:
    assert sorted(ChatHarvestRequest.model_fields.keys()) == _HARVEST_FIELDS
    assert sorted(ChatHarvestResponse.model_fields.keys()) == _RESPONSE_FIELDS
