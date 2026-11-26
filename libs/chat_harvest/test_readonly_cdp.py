"""Browser-free tests for read-only CDP transport (AC2)."""

from __future__ import annotations

import ast
import json
from pathlib import Path
from typing import Any

import pytest

from chat_harvest.readonly_cdp import (
    BROWSER_METHODS,
    COLLECT_SCRIPT,
    PAGE_METHODS,
    READY_SCRIPT,
    ReadonlyCDP,
    ReadOnlyViolation,
    set_open_connection,
)

pytestmark = pytest.mark.offline

_FORBIDDEN_METHODS = [
    "Input.dispatchMouseEvent",
    "Input.dispatchKeyEvent",
    "Input.insertText",
    "Page.navigate",
    "Page.reload",
    "Page.bringToFront",
    "Target.activateTarget",
    "Emulation.setFocusEmulationEnabled",
    "DOM.focus",
    "Runtime.callFunctionOn",
]


class FakeSocket:
    def __init__(
        self,
        *,
        evaluate_value: Any = None,
        exception_details: bool = False,
        protocol_error: bool = False,
    ) -> None:
        self.frames: list[dict[str, Any]] = []
        self._evaluate_value = evaluate_value if evaluate_value is not None else {}
        self._exception_details = exception_details
        self._protocol_error = protocol_error
        self._timeout = 5.0

    def send(self, data: str) -> None:
        self.frames.append(json.loads(data))

    def recv(self) -> str:
        msg_id = self.frames[-1]["id"]
        if self._protocol_error:
            return json.dumps({"id": msg_id, "error": "protocol_error"})
        if self._exception_details:
            return json.dumps(
                {"id": msg_id, "result": {"exceptionDetails": {"text": "script boom"}}}
            )
        return json.dumps({"id": msg_id, "result": {"result": {"value": self._evaluate_value}}})

    def settimeout(self, timeout: float) -> None:
        self._timeout = timeout

    def close(self) -> None:
        return None


def _fake_http(cdp: ReadonlyCDP, targets: list[dict[str, Any]] | None = None) -> None:
    def _get(url: str) -> Any:
        if url.endswith("/json/version"):
            return {"webSocketDebuggerUrl": "ws://127.0.0.1:9222/devtools/browser/abc"}
        if url.endswith("/json/list"):
            return targets or []
        raise AssertionError(url)

    cdp._http_get = _get  # noqa: SLF001


@pytest.fixture(autouse=True)
def _reset_open_connection() -> None:
    set_open_connection(None)
    yield
    set_open_connection(None)


def test_allowlists_are_exact() -> None:
    assert PAGE_METHODS == frozenset({"Runtime.evaluate"})
    assert BROWSER_METHODS == frozenset({"Target.createTarget", "Target.closeTarget"})


@pytest.mark.parametrize("method", _FORBIDDEN_METHODS)
def test_forbidden_method_raises_before_send(method: str) -> None:
    sock = FakeSocket()
    from chat_harvest import readonly_cdp as roc

    allowlist = BROWSER_METHODS if method.startswith("Target.") else PAGE_METHODS
    with pytest.raises(ReadOnlyViolation):
        roc._guarded_send(  # noqa: SLF001
            sock,
            method,
            {},
            allowlist=allowlist,
            frames=[],
            method_counts={},
        )
    assert sock.frames == []


def test_socket_send_only_in_guarded_function() -> None:
    repo = Path(__file__).resolve().parent.parent.parent
    modules = [
        repo / "libs/chat_harvest/readonly_cdp.py",
        repo / "libs/chat_harvest/dom_observation.py",
        repo / "libs/chat_harvest/dom_observation_shape.py",
        repo / "libs/chat_harvest/dom_observation_request.py",
        repo / "libs/chat_harvest/dom_observation_sanitize.py",
        repo / "libs/chat_harvest/dom_observation_poll.py",
        repo / "libs/cdp_ask/chat_session_observe.py",
    ]
    send_sites: list[str] = []
    for path in modules:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                if node.func.attr == "send":
                    send_sites.append(f"{path.name}:{node.lineno}")
    assert len(send_sites) == 1
    assert send_sites[0].startswith("readonly_cdp.py")


def test_evaluate_accepts_only_registered_scripts() -> None:
    sock = FakeSocket()
    cdp = ReadonlyCDP("http://127.0.0.1:9222")
    bogus = type("RegisteredScript", (), {"name": "x", "text": READY_SCRIPT.text})()
    for script in ("plain", READY_SCRIPT.text, bogus):
        with pytest.raises(ReadOnlyViolation):
            if isinstance(script, str):
                cdp.evaluate(sock, type("S", (), {"text": script, "name": "s"})(), {})
            else:
                cdp.evaluate(sock, script, {})
    assert sock.frames == []


def test_wire_expression_is_script_applied_to_json_argument() -> None:
    def _open(_url: str, **_kwargs: Any) -> FakeSocket:
        return FakeSocket(evaluate_value={"ok": True})

    set_open_connection(_open)
    cdp = ReadonlyCDP("http://127.0.0.1:9222")
    sock = _open("ws://x")
    arg = {"match_terms": ["pause"], "max_nodes": 3}
    for script in (READY_SCRIPT, COLLECT_SCRIPT):
        sock.frames.clear()
        cdp.evaluate(sock, script, arg)
        expr = sock.frames[-1]["params"]["expression"]
        assert expr == "(" + script.text + ")(" + json.dumps(arg, ensure_ascii=True) + ")"


def test_existing_tab_receives_only_evaluate() -> None:
    target = {
        "type": "page",
        "id": "p1",
        "url": "https://claude.ai/chat/thread-uuid",
        "webSocketDebuggerUrl": "ws://127.0.0.1:9222/devtools/page/p1",
    }
    cdp = ReadonlyCDP("http://127.0.0.1:9222")
    _fake_http(cdp, [target])

    def _open(ws: str, **_kwargs: Any) -> FakeSocket:
        assert "page" in ws
        return FakeSocket(evaluate_value={"turn_nodes": 1, "element_count": 10, "login_wall": False})

    set_open_connection(_open)
    found = cdp.find_matching_target("https://claude.ai/chat/thread-uuid")
    assert found is not None
    conn = cdp._page_connection(found)  # noqa: SLF001
    cdp.evaluate(conn, READY_SCRIPT, {"message_sel": []})
    assert all(f["method"] == "Runtime.evaluate" for f in cdp.frames)
    for frame in cdp.frames:
        params = frame["params"]
        assert params.get("userGesture") is False
        assert params.get("returnByValue") is True
        assert params.get("awaitPromise") is False
        assert params.get("includeCommandLineAPI") is False
    assert not any(f["method"] in BROWSER_METHODS for f in cdp.frames)


def test_no_tab_mints_once_and_closes_even_on_error(monkeypatch: pytest.MonkeyPatch) -> None:
    from chat_harvest.dom_observation import run_dom_observation

    chat_url = "https://claude.ai/chat/thread-uuid"
    minted_id = "target-99"
    page_ws = "ws://127.0.0.1:9222/devtools/page/minted"
    cdp = ReadonlyCDP("http://127.0.0.1:9222")

    state = {"minted": False}

    def _get(url: str) -> Any:
        if url.endswith("/json/version"):
            return {"webSocketDebuggerUrl": "ws://127.0.0.1:9222/devtools/browser/abc"}
        if url.endswith("/json/list"):
            if state["minted"]:
                return [
                    {
                        "type": "page",
                        "id": minted_id,
                        "targetId": minted_id,
                        "url": chat_url,
                        "webSocketDebuggerUrl": page_ws,
                    }
                ]
            return []
        raise AssertionError(url)

    cdp._http_get = _get  # noqa: SLF001

    def _open(ws: str, **_kwargs: Any) -> FakeSocket:
        if "browser" in ws:
            sock = FakeSocket()

            def _recv() -> str:
                mid = sock.frames[-1]["id"]
                if sock.frames[-1]["method"] == "Target.createTarget":
                    state["minted"] = True
                    return json.dumps({"id": mid, "result": {"targetId": minted_id}})
                if sock.frames[-1]["method"] == "Target.closeTarget":
                    return json.dumps({"id": mid, "result": {}})
                return json.dumps({"id": mid, "result": {"result": {"value": {}}}})

            sock.recv = _recv  # type: ignore[method-assign]
            return sock

        class PageSock(FakeSocket):
            def recv(self) -> str:
                if len(self.frames) >= 5:
                    return super().recv()
                return json.dumps(
                    {
                        "id": self.frames[-1]["id"],
                        "result": {
                            "result": {
                                "value": {
                                    "turn_nodes": 1,
                                    "element_count": 1,
                                    "login_wall": False,
                                }
                            }
                        },
                    }
                )

        return PageSock(exception_details=True)

    set_open_connection(_open)
    monkeypatch.setattr("chat_harvest.dom_observation_poll.time.sleep", lambda _s: None)
    out = run_dom_observation(
        chat_url=chat_url,
        conversation_id="thread-uuid",
        cdp_url="http://127.0.0.1:9222",
        match_terms=["zz"],
        max_nodes=20,
        open_on_demand=True,
        scope="conversation:thread-uuid",
        transport=cdp,
    )
    create_frames = [f for f in cdp.frames if f["method"] == "Target.createTarget"]
    close_frames = [f for f in cdp.frames if f["method"] == "Target.closeTarget"]
    assert len(create_frames) == 1
    assert create_frames[0]["params"] == {"url": chat_url, "background": True}
    assert len(close_frames) == 1
    assert close_frames[0]["params"] == {"targetId": minted_id}
    assert out["outcome"] == "unreachable"
    assert out["code"] == "collector_error"


def test_open_on_demand_false_without_match_opens_no_socket() -> None:
    from chat_harvest.dom_observation import run_dom_observation

    cdp = ReadonlyCDP("http://127.0.0.1:9222")
    _fake_http(cdp, [])
    set_open_connection(lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("no socket")))
    out = run_dom_observation(
        chat_url="https://claude.ai/chat/thread-uuid",
        conversation_id="thread-uuid",
        cdp_url="http://127.0.0.1:9222",
        match_terms=["zz"],
        max_nodes=20,
        open_on_demand=False,
        scope="conversation:thread-uuid",
        transport=cdp,
    )
    assert out["outcome"] == "no_tab"
    assert cdp.frames == []


def test_close_refuses_target_it_did_not_open() -> None:
    cdp = ReadonlyCDP("http://127.0.0.1:9222")
    with pytest.raises(ReadOnlyViolation):
        cdp.close_tab("foreign-target")


def test_http_get_refuses_paths_outside_allowlist() -> None:
    cdp = ReadonlyCDP("http://127.0.0.1:9222")
    for path in ("/json/new", "/json/activate/x", "/json/close/x"):
        with pytest.raises(ReadOnlyViolation):
            cdp.http_get_json(path)
