"""DOM observation shaping and validation tests (AC1, AC7)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from chat_harvest.dom_observation import run_dom_observation
from chat_harvest.dom_observation_request import (
    DEFAULT_MAX_NODES,
    ChatObserveRequest,
    InvalidPattern,
    script_config,
    validate_match_terms,
)
from chat_harvest.dom_observation_sanitize import shape_control, shape_node
from chat_harvest.dom_observation_shape import (
    build_observation_block,
    cap_observation_size,
)
from chat_harvest.readonly_cdp import (
    COLLECT_SCRIPT,
    ReadonlyCDP,
    set_open_connection,
)

pytestmark = pytest.mark.offline

T6_FORBIDDEN = [
    ".click(",
    ".focus(",
    ".blur(",
    ".submit(",
    "dispatchEvent",
    "scrollTo",
    "scrollBy",
    "scrollIntoView",
    "scrollTop",
    "location.assign",
    "location.replace",
    "location.reload",
    "location.href =",
    "history.",
    "fetch(",
    "XMLHttpRequest",
    "sendBeacon",
    "WebSocket",
    "localStorage",
    "sessionStorage",
    "document.cookie",
    "innerHTML",
    "outerHTML",
    "setAttribute",
    "removeAttribute",
    "appendChild",
    "removeChild",
    ".remove(",
    ".value =",
    "eval(",
    "Function(",
    "setTimeout",
    "setInterval",
    "postMessage",
    "window.open",
    "execCommand",
]


def test_node_descriptor_fields() -> None:
    raw = {
        "index": 0,
        "region": "outside",
        "tag": "div",
        "role": "alert",
        "data_testid": "card",
        "aria": {"aria-live": "polite"},
        "class_prefix": "flex",
        "text": {"page_text": "paused", "chars": 6, "cut": False},
        "controls": [],
        "position": {"rect": [0, 0, 10, 10], "in_viewport": True, "visible": True},
        "path": ["main"],
    }
    shaped = shape_node(raw)
    for key in ("tag", "role", "data_testid", "aria", "class_prefix", "text", "controls", "position"):
        assert key in shaped
    assert shaped["text"]["content_kind"] if "content_kind" in shaped["text"] else True


def test_page_text_cut_at_500_and_sanitized() -> None:
    long_text = ("word " * 400).strip()
    shaped = shape_node(
        {
            "text": {"page_text": long_text, "chars": 2000, "cut": False},
            "controls": [],
        }
    )
    assert shaped["text"]["chars"] == 2000
    assert shaped["text"]["cut"] is True
    assert len(shaped["text"]["page_text"]) == 500


def test_empty_result_is_explicit() -> None:
    obs = build_observation_block(
        looked=True,
        raw_collect={"nodes": [], "counts": {"total": 0}, "page": {}},
        query={"match_terms": ["none"], "max_nodes": 20, "scope": "conversation:x"},
        readiness={"polls": 1, "waited_ms": 0, "turns_seen": True, "stable": True},
        tab={"matching_tabs": 0, "origin": "opened_on_demand"},
        cdp_methods={"Runtime.evaluate": 2},
    )
    assert obs["looked"] is True
    assert obs["counts"]["total"] == 0
    assert obs["nodes"] == []


def test_transport_failure_reports_not_looked() -> None:
    cdp = ReadonlyCDP("http://127.0.0.1:9222")

    def _boom(_url: str) -> None:
        raise OSError("down")

    cdp._http_get = _boom  # noqa: SLF001
    out = run_dom_observation(
        chat_url="https://claude.ai/chat/x",
        conversation_id="x",
        cdp_url="http://127.0.0.1:9222",
        match_terms=["a"],
        max_nodes=20,
        open_on_demand=True,
        scope="conversation:x",
        transport=cdp,
    )
    assert out["outcome"] == "unreachable"
    assert out["observation"]["looked"] is False


def test_login_wall_reports_unauthenticated() -> None:
    target = {
        "type": "page",
        "id": "p1",
        "url": "https://claude.ai/chat/x",
        "webSocketDebuggerUrl": "ws://127.0.0.1:9222/devtools/page/p1",
    }
    cdp = ReadonlyCDP("http://127.0.0.1:9222")
    cdp._http_get = lambda url: (  # noqa: SLF001
        [target] if url.endswith("/json/list") else {"webSocketDebuggerUrl": "ws://b"}
    )
    from chat_harvest.test_readonly_cdp import FakeSocket

    set_open_connection(lambda *_a, **_k: FakeSocket(evaluate_value={"login_wall": True, "turn_nodes": 0}))
    out = run_dom_observation(
        chat_url="https://claude.ai/chat/x",
        conversation_id="x",
        cdp_url="http://127.0.0.1:9222",
        match_terms=["a"],
        max_nodes=20,
        open_on_demand=False,
        scope="conversation:x",
        transport=cdp,
    )
    assert out["outcome"] == "unauthenticated"
    assert out["observation"]["looked"] is False


def test_hostile_term_stays_inside_json_argument() -> None:
    from chat_harvest.test_readonly_cdp import FakeSocket

    sock = FakeSocket()
    term = '");location.reload();//'
    arg = {"match_terms": [term], "max_nodes": 5}
    expr = "(" + COLLECT_SCRIPT.text + ")(" + json.dumps(arg, ensure_ascii=True) + ")"
    from chat_harvest.readonly_cdp import ReadonlyCDP

    cdp = ReadonlyCDP("http://127.0.0.1:9222")
    cdp.evaluate(sock, COLLECT_SCRIPT, arg)
    recorded = sock.frames[-1]["params"]["expression"]
    assert recorded == expr
    inner = recorded.rsplit(")(", 1)[1][:-1]
    assert json.loads(inner) == arg


def test_max_nodes_default_20_and_inside_message_cap_5() -> None:
    """Default max_nodes is 20; inside-message cap is enforced in the collect script source."""
    assert ChatObserveRequest.model_fields["max_nodes"].default == DEFAULT_MAX_NODES == 20
    collect_js = (Path(__file__).resolve().parent / "dom_observation_collect.js").read_text(
        encoding="utf-8"
    )
    assert "Math.min(5, maxNodes)" in collect_js


def test_observation_capped_at_64_kib_sets_truncated() -> None:
    huge_nodes = [
        {"text": {"page_text": "word " * 800, "chars": 4000, "cut": True}, "controls": []}
        for _ in range(120)
    ]
    obs = {
        "looked": True,
        "nodes": huge_nodes,
        "counts": {"total": 120, "returned": 120},
        "query": {"match_terms": ["x"], "max_nodes": 120},
        "readiness": {"polls": 1, "waited_ms": 0, "turns_seen": True, "stable": True},
        "tab": {},
        "cdp_methods": {},
        "page": {},
    }
    capped = cap_observation_size(obs)
    assert capped["truncated"] is True
    assert len(json.dumps(capped, ensure_ascii=True).encode()) <= 65536


def test_readiness_stops_at_budget(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = {"n": 0}

    def _mono() -> float:
        calls["n"] += 1
        return float(calls["n"] * 2)

    target = {
        "type": "page",
        "id": "p1",
        "url": "https://claude.ai/chat/x",
        "webSocketDebuggerUrl": "ws://127.0.0.1:9222/devtools/page/p1",
    }
    cdp = ReadonlyCDP("http://127.0.0.1:9222")
    cdp._http_get = lambda url: (  # noqa: SLF001
        [target] if url.endswith("/json/list") else {"webSocketDebuggerUrl": "ws://b"}
    )
    from chat_harvest.test_readonly_cdp import FakeSocket

    collect_prefix = "(" + COLLECT_SCRIPT.text

    class SeqSock(FakeSocket):
        def recv(self) -> str:
            expr = self.frames[-1]["params"]["expression"]
            if expr.startswith(collect_prefix):
                value = {
                    "nodes": [],
                    "counts": {"total": 7, "returned": 0},
                    "page": {},
                    "containers": [],
                }
            else:
                value = {"turn_nodes": 0, "element_count": 1, "login_wall": False}
            msg_id = self.frames[-1]["id"]
            return json.dumps({"id": msg_id, "result": {"result": {"value": value}}})

    set_open_connection(lambda *_a, **_k: SeqSock())
    monkeypatch.setattr("chat_harvest.dom_observation_poll.time.sleep", lambda _s: None)
    out = run_dom_observation(
        chat_url="https://claude.ai/chat/x",
        conversation_id="x",
        cdp_url="http://127.0.0.1:9222",
        match_terms=["z"],
        max_nodes=20,
        open_on_demand=False,
        scope="conversation:x",
        transport=cdp,
        monotonic=_mono,
    )
    assert out["outcome"] == "observed"
    assert out["observation"]["readiness"]["turns_seen"] is False
    collect_frames = [
        f
        for f in cdp.frames
        if f["method"] == "Runtime.evaluate"
        and f["params"]["expression"].startswith(collect_prefix)
    ]
    assert len(collect_frames) == 1
    assert out["observation"]["counts"]["total"] == 7


def test_wall_clock_budget_sets_code_timeout_and_closes_minted_tab(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    t = {"now": 0.0}
    minted_id = "mint-timeout"
    chat_url = "https://claude.ai/chat/x"
    state = {"minted": False}

    def _mono() -> float:
        t["now"] += 40.0
        return t["now"]

    cdp = ReadonlyCDP("http://127.0.0.1:9222")

    def _http_get(url: str) -> Any:
        if url.endswith("/json/list"):
            if state["minted"]:
                return [
                    {
                        "type": "page",
                        "id": minted_id,
                        "targetId": minted_id,
                        "url": chat_url,
                        "webSocketDebuggerUrl": "ws://127.0.0.1:9222/devtools/page/minted",
                    }
                ]
            return []
        return {"webSocketDebuggerUrl": "ws://127.0.0.1:9222/devtools/browser/abc"}

    cdp._http_get = _http_get  # noqa: SLF001
    from chat_harvest.test_readonly_cdp import FakeSocket

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
        return FakeSocket(evaluate_value={"turn_nodes": 0, "element_count": 1, "login_wall": False})

    set_open_connection(_open)
    monkeypatch.setattr("chat_harvest.dom_observation_poll.time.sleep", lambda _s: None)
    out = run_dom_observation(
        chat_url=chat_url,
        conversation_id="x",
        cdp_url="http://127.0.0.1:9222",
        match_terms=["z"],
        max_nodes=20,
        open_on_demand=True,
        scope="conversation:x",
        transport=cdp,
        monotonic=_mono,
    )
    assert out.get("code") == "timeout"
    assert out["outcome"] == "unreachable"
    close_frames = [f for f in cdp.frames if f["method"] == "Target.closeTarget"]
    assert len(close_frames) == 1
    assert close_frames[0]["params"]["targetId"] == minted_id
    assert out["observation"]["tab"]["minted_tab_closed"] is True


def test_scripts_contain_no_forbidden_tokens() -> None:
    root = Path(__file__).resolve().parent
    for name in ("dom_observation_ready.js", "dom_observation_collect.js"):
        text = (root / name).read_text(encoding="utf-8")
        for token in T6_FORBIDDEN:
            assert token not in text, f"{name} contains {token!r}"


def test_modules_have_no_forbidden_imports() -> None:
    import ast

    repo = Path(__file__).resolve().parent.parent.parent
    forbidden = frozenset(
        {
            "playwright",
            "claude_bundles.skills_ui_panel",
            "claude_bundles.project_ask",
            "chat_harvest.claude_chat_adapter",
        }
    )
    modules = [
        repo / "libs/chat_harvest/readonly_cdp.py",
        repo / "libs/chat_harvest/dom_observation.py",
        repo / "libs/chat_harvest/dom_observation_shape.py",
        repo / "libs/chat_harvest/dom_observation_request.py",
        repo / "libs/chat_harvest/dom_observation_sanitize.py",
        repo / "libs/chat_harvest/dom_observation_poll.py",
        repo / "libs/cdp_ask/chat_session_observe.py",
    ]
    for path in modules:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                for part in forbidden:
                    assert not (
                        node.module == part or node.module.startswith(part + ".")
                    ), f"{path.name} imports {node.module}"
            if isinstance(node, ast.Import):
                for alias in node.names:
                    mod = alias.name.split(".")[0]
                    for part in forbidden:
                        assert not (
                            mod == part or alias.name.startswith(part + ".")
                        ), f"{path.name} imports {alias.name}"


def test_invalid_terms_raise() -> None:
    with pytest.raises(InvalidPattern):
        validate_match_terms([])
    with pytest.raises(InvalidPattern):
        validate_match_terms(["x"])


def test_minted_target_not_in_list_still_closes(monkeypatch: pytest.MonkeyPatch) -> None:
    minted_id = "ghost-tab"
    chat_url = "https://claude.ai/chat/ghost"
    cdp = ReadonlyCDP("http://127.0.0.1:9222")
    calls = {"n": 0}

    def _http_get(url: str) -> Any:
        if url.endswith("/json/version"):
            return {"webSocketDebuggerUrl": "ws://browser"}
        if url.endswith("/json/list"):
            calls["n"] += 1
            return [] if calls["n"] <= 2 else []
        raise AssertionError(url)

    cdp._http_get = _http_get  # noqa: SLF001
    from chat_harvest.test_readonly_cdp import FakeSocket

    def _open(ws: str, **_kwargs: Any) -> FakeSocket:
        if "browser" in ws:
            sock = FakeSocket()

            def _recv() -> str:
                mid = sock.frames[-1]["id"]
                if sock.frames[-1]["method"] == "Target.createTarget":
                    return json.dumps({"id": mid, "result": {"targetId": minted_id}})
                if sock.frames[-1]["method"] == "Target.closeTarget":
                    return json.dumps({"id": mid, "result": {}})
                return json.dumps({"id": mid, "result": {}})

            sock.recv = _recv  # type: ignore[method-assign]
            return sock
        return FakeSocket()

    set_open_connection(_open)
    monkeypatch.setattr("chat_harvest.dom_observation_poll.time.sleep", lambda _s: None)
    out = run_dom_observation(
        chat_url=chat_url,
        conversation_id="ghost",
        cdp_url="http://127.0.0.1:9222",
        match_terms=["zz"],
        max_nodes=20,
        open_on_demand=True,
        scope="conversation:ghost",
        transport=cdp,
    )
    close_frames = [f for f in cdp.frames if f["method"] == "Target.closeTarget"]
    assert len(close_frames) == 1
    assert out["opened_on_demand"] is True
    assert out["observation"]["tab"]["origin"] == "opened_on_demand"
    assert out["observation"]["tab"]["target_id"] == minted_id


def test_close_target_error_stamps_minted_tab_closed_false(monkeypatch: pytest.MonkeyPatch) -> None:
    minted_id = "m2"
    chat_url = "https://claude.ai/chat/m2"
    cdp = ReadonlyCDP("http://127.0.0.1:9222")

    def _http_get(url: str) -> Any:
        if url.endswith("/json/version"):
            return {"webSocketDebuggerUrl": "ws://browser"}
        if url.endswith("/json/list"):
            return []
        raise AssertionError(url)

    cdp._http_get = _http_get  # noqa: SLF001
    from chat_harvest.test_readonly_cdp import FakeSocket

    def _open(ws: str, **_kwargs: Any) -> FakeSocket:
        if "browser" in ws:
            sock = FakeSocket()

            def _recv() -> str:
                mid = sock.frames[-1]["id"]
                method = sock.frames[-1]["method"]
                if method == "Target.createTarget":
                    return json.dumps({"id": mid, "result": {"targetId": minted_id}})
                if method == "Target.closeTarget":
                    return json.dumps({"id": mid, "error": "close failed"})
                return json.dumps({"id": mid, "result": {}})

            sock.recv = _recv  # type: ignore[method-assign]
            return sock
        return FakeSocket()

    set_open_connection(_open)
    monkeypatch.setattr("chat_harvest.dom_observation_poll.time.sleep", lambda _s: None)
    out = run_dom_observation(
        chat_url=chat_url,
        conversation_id="m2",
        cdp_url="http://127.0.0.1:9222",
        match_terms=["zz"],
        max_nodes=20,
        open_on_demand=True,
        scope="conversation:m2",
        transport=cdp,
    )
    assert out["observation"]["tab"]["minted_tab_closed"] is False
    assert out["observation"]["tab"]["target_id"] == minted_id


def test_collect_exception_details_is_unreachable(monkeypatch: pytest.MonkeyPatch) -> None:
    target = {
        "type": "page",
        "id": "p1",
        "url": "https://claude.ai/chat/x",
        "webSocketDebuggerUrl": "ws://page",
    }
    cdp = ReadonlyCDP("http://127.0.0.1:9222")
    cdp._http_get = lambda url: (  # noqa: SLF001
        [target] if url.endswith("/json/list") else {"webSocketDebuggerUrl": "ws://b"}
    )
    from chat_harvest.test_readonly_cdp import FakeSocket

    ready = {"turn_nodes": 1, "element_count": 1, "login_wall": False}

    class SeqSock(FakeSocket):
        def recv(self) -> str:
            exc = len(self.frames) >= 5
            msg_id = self.frames[-1]["id"]
            if exc:
                return json.dumps(
                    {"id": msg_id, "result": {"exceptionDetails": {"text": "boom"}}}
                )
            return json.dumps({"id": msg_id, "result": {"result": {"value": ready}}})

    set_open_connection(lambda *_a, **_k: SeqSock())
    monkeypatch.setattr("chat_harvest.dom_observation_poll.time.sleep", lambda _s: None)
    out = run_dom_observation(
        chat_url="https://claude.ai/chat/x",
        conversation_id="x",
        cdp_url="http://127.0.0.1:9222",
        match_terms=["a"],
        max_nodes=20,
        open_on_demand=False,
        scope="conversation:x",
        transport=cdp,
    )
    assert out["outcome"] == "unreachable"
    assert out["code"] == "collector_error"
    assert out["observation"]["looked"] is False
    assert out["observation"]["tab"]["minted_tab_closed"] is None


def test_ready_exception_stops_after_one_evaluate(monkeypatch: pytest.MonkeyPatch) -> None:
    target = {
        "type": "page",
        "id": "p1",
        "url": "https://claude.ai/chat/x",
        "webSocketDebuggerUrl": "ws://page",
    }
    cdp = ReadonlyCDP("http://127.0.0.1:9222")
    cdp._http_get = lambda url: (  # noqa: SLF001
        [target] if url.endswith("/json/list") else {"webSocketDebuggerUrl": "ws://b"}
    )
    from chat_harvest.test_readonly_cdp import FakeSocket

    set_open_connection(lambda *_a, **_k: FakeSocket(exception_details=True))
    monkeypatch.setattr("chat_harvest.dom_observation_poll.time.sleep", lambda _s: None)
    out = run_dom_observation(
        chat_url="https://claude.ai/chat/x",
        conversation_id="x",
        cdp_url="http://127.0.0.1:9222",
        match_terms=["a"],
        max_nodes=20,
        open_on_demand=False,
        scope="conversation:x",
        transport=cdp,
    )
    evaluate_frames = [f for f in cdp.frames if f["method"] == "Runtime.evaluate"]
    assert len(evaluate_frames) == 1
    assert out["code"] == "collector_error"


def test_email_redacted_in_controls_aria_and_outside() -> None:
    raw_collect = {
        "nodes": [
            {
                "text": {"page_text": "x", "chars": 1, "cut": False},
                "controls": [
                    {
                        "text": {"page_text": "user@evil.com", "chars": 13, "cut": False},
                        "aria_label": "user@evil.com",
                    }
                ],
                "aria": {"aria-label": "user@evil.com"},
            }
        ],
        "containers": [
            {
                "text": {"page_text": "c", "chars": 1, "cut": False},
                "controls": [
                    {"text": {"page_text": "user@evil.com", "chars": 13, "cut": False}, "aria_label": "x"}
                ],
                "aria": {},
            }
        ],
        "controls_outside_messages": [
            {"text": {"page_text": "user@evil.com", "chars": 13, "cut": False}, "aria_label": "user@evil.com"}
        ],
        "counts": {"total": 1, "returned": 1},
        "page": {},
    }
    obs = build_observation_block(
        looked=True,
        raw_collect=raw_collect,
        query={"match_terms": ["x"], "max_nodes": 5, "scope": "s"},
        readiness={},
        tab={},
        cdp_methods={},
    )
    blob = json.dumps(obs, ensure_ascii=True)
    assert "user@evil.com" not in blob
    assert blob.count("<redacted-email>") >= 3


def test_shaping_retains_b_fields() -> None:
    raw_collect = {
        "nodes": [
            {
                "text": {"page_text": "hit", "chars": 3, "cut": False},
                "controls": [],
                "aria": {},
                "container_index": 0,
                "position": {
                    "after_last_message_node": True,
                    "after_last_user_message": None,
                },
            }
        ],
        "containers": [{"text": {"page_text": "wrap", "chars": 4, "cut": False}, "controls": [], "aria": {}}],
        "counts": {"total": 1, "returned": 1, "outside": 1},
        "page": {
            "turn_nodes": 2,
            "user_turn_nodes": 1,
            "text_nodes_scanned": 99,
            "scan_capped": True,
            "url": {"page_text": "https://claude.ai/chat/x", "chars": 24, "cut": False},
            "time_origin_ms": 12345,
        },
        "controls_outside_messages": [],
    }
    obs = build_observation_block(
        looked=True,
        raw_collect=raw_collect,
        query={"match_terms": ["hit"], "max_nodes": 5, "scope": "conversation:x"},
        readiness={},
        tab={},
        cdp_methods={},
    )
    assert obs["nodes"][0]["container_index"] == 0
    assert obs["containers"][0]["text"]["page_text"] == "wrap"
    assert obs["page"]["scan_capped"] is True
    assert obs["page"]["url"]["page_text"].startswith("https://")
    assert "time_origin_ms" not in obs["page"]


def test_cap_drop_order_preserves_container_indexes() -> None:
    pad = "x" * 3000
    nodes = [
        {
            "text": {"page_text": f"n{i}{pad}", "chars": len(f"n{i}{pad}"), "cut": True},
            "controls": [],
            "aria": {},
            "container_index": i,
        }
        for i in range(30)
    ]
    containers = [
        {"text": {"page_text": f"c{i}", "chars": 3, "cut": False}, "controls": [], "aria": {}}
        for i in range(30)
    ]
    outside = [
        {
            "text": {"page_text": f"o{i}{pad}", "chars": len(f"o{i}{pad}"), "cut": False},
            "aria_label": None,
        }
        for i in range(10)
    ]
    obs = {
        "looked": True,
        "nodes": nodes,
        "containers": containers,
        "controls_outside_messages": outside,
        "counts": {"total": 30, "returned": 30},
        "query": {},
        "readiness": {},
        "tab": {},
        "cdp_methods": {},
        "page": {},
    }
    capped = cap_observation_size(obs)
    kept = capped["nodes"]
    dropped = 30 - len(kept)
    assert kept == nodes[: len(kept)]
    assert capped.get("dropped") == dropped
    assert capped["truncated"] is True
    refs = {n["container_index"] for n in kept if n.get("container_index") is not None}
    for idx in refs:
        assert idx < len(capped.get("containers") or [])
    assert len(capped.get("controls_outside_messages") or []) == len(outside)
    assert len(json.dumps(capped, ensure_ascii=True).encode()) <= 65536


def test_existing_tab_mid_collect_recv_error_unreachable(monkeypatch: pytest.MonkeyPatch) -> None:
    target = {
        "type": "page",
        "id": "p1",
        "url": "https://claude.ai/chat/x",
        "webSocketDebuggerUrl": "ws://page",
    }
    cdp = ReadonlyCDP("http://127.0.0.1:9222")
    cdp._http_get = lambda url: (  # noqa: SLF001
        [target] if url.endswith("/json/list") else {"webSocketDebuggerUrl": "ws://b"}
    )
    from chat_harvest.test_readonly_cdp import FakeSocket

    class BoomSock(FakeSocket):
        def recv(self) -> str:
            if len(self.frames) >= 5:
                raise OSError("socket gone")
            return super().recv()

    set_open_connection(
        lambda *_a, **_k: BoomSock(
            evaluate_value={"turn_nodes": 1, "element_count": 1, "login_wall": False}
        )
    )
    monkeypatch.setattr("chat_harvest.dom_observation_poll.time.sleep", lambda _s: None)
    out = run_dom_observation(
        chat_url="https://claude.ai/chat/x",
        conversation_id="x",
        cdp_url="http://127.0.0.1:9222",
        match_terms=["a"],
        max_nodes=20,
        open_on_demand=False,
        scope="conversation:x",
        transport=cdp,
    )
    assert out["outcome"] == "unreachable"
    assert out["observation"]["looked"] is False
    assert not any(f["method"].startswith("Target.") for f in cdp.frames)


def test_mint_success_stamps_cdp_method_counts(monkeypatch: pytest.MonkeyPatch) -> None:
    minted_id = "mint-ok"
    chat_url = "https://claude.ai/chat/mint-ok"
    state = {"minted": False}
    cdp = ReadonlyCDP("http://127.0.0.1:9222")

    def _http_get(url: str) -> Any:
        if url.endswith("/json/version"):
            return {"webSocketDebuggerUrl": "ws://browser"}
        if url.endswith("/json/list"):
            if state["minted"]:
                return [
                    {
                        "type": "page",
                        "id": minted_id,
                        "targetId": minted_id,
                        "url": chat_url,
                        "webSocketDebuggerUrl": "ws://page",
                    }
                ]
            return []
        raise AssertionError(url)

    cdp._http_get = _http_get  # noqa: SLF001
    from chat_harvest.test_readonly_cdp import FakeSocket

    collect_prefix = "(" + COLLECT_SCRIPT.text
    collect_val = {
        "nodes": [],
        "counts": {"total": 0, "returned": 0},
        "page": {"time_origin_ms": 1795680000000},
        "containers": [],
    }

    def _open(ws: str, **_kwargs: Any) -> FakeSocket:
        if "browser" in ws:
            sock = FakeSocket()

            def _recv() -> str:
                mid = sock.frames[-1]["id"]
                method = sock.frames[-1]["method"]
                if method == "Target.createTarget":
                    state["minted"] = True
                    return json.dumps({"id": mid, "result": {"targetId": minted_id}})
                if method == "Target.closeTarget":
                    return json.dumps({"id": mid, "result": {"success": True}})
                return json.dumps({"id": mid, "result": {}})

            sock.recv = _recv  # type: ignore[method-assign]
            return sock

        class PageSock(FakeSocket):
            def recv(self) -> str:
                expr = self.frames[-1]["params"]["expression"]
                mid = self.frames[-1]["id"]
                if expr.startswith(collect_prefix):
                    val = collect_val
                else:
                    val = {"turn_nodes": 1, "element_count": 5, "login_wall": False}
                return json.dumps({"id": mid, "result": {"result": {"value": val}}})

        return PageSock()

    set_open_connection(_open)
    monkeypatch.setattr("chat_harvest.dom_observation_poll.time.sleep", lambda _s: None)
    out = run_dom_observation(
        chat_url=chat_url,
        conversation_id="mint-ok",
        cdp_url="http://127.0.0.1:9222",
        match_terms=["a"],
        max_nodes=20,
        open_on_demand=True,
        scope="conversation:mint-ok",
        transport=cdp,
    )
    assert out["outcome"] == "observed"
    assert out["observation"]["tab"]["time_origin_ms"] == 1795680000000
    assert "time_origin_ms" not in out["observation"]["page"]
    n_eval = sum(1 for f in cdp.frames if f["method"] == "Runtime.evaluate")
    assert out["observation"]["cdp_methods"] == {
        "Runtime.evaluate": n_eval,
        "Target.createTarget": 1,
        "Target.closeTarget": 1,
    }


def test_time_origin_ms_on_tab_not_page_existing(monkeypatch: pytest.MonkeyPatch) -> None:
    target = {
        "type": "page",
        "id": "p1",
        "url": "https://claude.ai/chat/x",
        "webSocketDebuggerUrl": "ws://page",
    }
    cdp = ReadonlyCDP("http://127.0.0.1:9222")
    cdp._http_get = lambda url: (  # noqa: SLF001
        [target] if url.endswith("/json/list") else {"webSocketDebuggerUrl": "ws://b"}
    )
    from chat_harvest.test_readonly_cdp import FakeSocket

    collect_prefix = "(" + COLLECT_SCRIPT.text

    class PageSock(FakeSocket):
        def recv(self) -> str:
            expr = self.frames[-1]["params"]["expression"]
            mid = self.frames[-1]["id"]
            if expr.startswith(collect_prefix):
                val = {
                    "nodes": [],
                    "counts": {"total": 0, "returned": 0},
                    "page": {"time_origin_ms": 1795680000000},
                    "containers": [],
                }
            else:
                val = {"turn_nodes": 1, "element_count": 1, "login_wall": False}
            return json.dumps({"id": mid, "result": {"result": {"value": val}}})

    set_open_connection(lambda *_a, **_k: PageSock())
    monkeypatch.setattr("chat_harvest.dom_observation_poll.time.sleep", lambda _s: None)
    out = run_dom_observation(
        chat_url="https://claude.ai/chat/x",
        conversation_id="x",
        cdp_url="http://127.0.0.1:9222",
        match_terms=["a"],
        max_nodes=20,
        open_on_demand=False,
        scope="conversation:x",
        transport=cdp,
    )
    assert out["observation"]["tab"]["time_origin_ms"] == 1795680000000
    assert "time_origin_ms" not in out["observation"]["page"]


def test_role_and_data_testid_capped_at_120() -> None:
    long = "r" * 500
    node = shape_node(
        {"text": {"page_text": "t", "chars": 1, "cut": False}, "controls": [], "role": long, "data_testid": long}
    )
    ctrl = shape_control(
        {"text": {"page_text": "t", "chars": 1, "cut": False}, "role": long, "aria_label": None}
    )
    none_node = shape_node(
        {"text": {"page_text": "t", "chars": 1, "cut": False}, "controls": [], "role": None, "data_testid": None}
    )
    assert len(node["role"]) == 120
    assert len(node["data_testid"]) == 120
    assert len(ctrl["role"]) == 120
    assert none_node["role"] is None
    assert none_node["data_testid"] is None


def test_cap_outside_only_sets_truncated_and_dropped_outside() -> None:
    pad = "z" * 4000
    obs = {
        "looked": True,
        "nodes": [],
        "containers": [],
        "controls_outside_messages": [
            {"text": {"page_text": f"{i}{pad}", "chars": len(f"{i}{pad}"), "cut": True}, "aria_label": None}
            for i in range(20)
        ],
        "counts": {"total": 0, "returned": 0},
        "query": {},
        "readiness": {},
        "tab": {},
        "cdp_methods": {},
        "page": {},
    }
    capped = cap_observation_size(obs)
    assert capped["truncated"] is True
    assert capped.get("dropped_outside", 0) > 0
    assert len(json.dumps(capped, ensure_ascii=True).encode()) <= 65536


def test_ready_non_object_is_collector_error(monkeypatch: pytest.MonkeyPatch) -> None:
    target = {
        "type": "page",
        "id": "p1",
        "url": "https://claude.ai/chat/x",
        "webSocketDebuggerUrl": "ws://page",
    }
    cdp = ReadonlyCDP("http://127.0.0.1:9222")
    cdp._http_get = lambda url: (  # noqa: SLF001
        [target] if url.endswith("/json/list") else {"webSocketDebuggerUrl": "ws://b"}
    )
    from chat_harvest.test_readonly_cdp import FakeSocket

    set_open_connection(lambda *_a, **_k: FakeSocket(evaluate_value="x"))
    monkeypatch.setattr("chat_harvest.dom_observation_poll.time.sleep", lambda _s: None)
    out = run_dom_observation(
        chat_url="https://claude.ai/chat/x",
        conversation_id="x",
        cdp_url="http://127.0.0.1:9222",
        match_terms=["a"],
        max_nodes=20,
        open_on_demand=False,
        scope="conversation:x",
        transport=cdp,
    )
    assert out["outcome"] == "unreachable"
    assert out["code"] == "collector_error"
    assert out["observation"]["looked"] is False
    assert sum(1 for f in cdp.frames if f["method"] == "Runtime.evaluate") == 1


def test_page_url_matches_request(monkeypatch: pytest.MonkeyPatch) -> None:
    target = {
        "type": "page",
        "id": "p1",
        "url": "https://claude.ai/chat/x",
        "webSocketDebuggerUrl": "ws://page",
    }
    cdp = ReadonlyCDP("http://127.0.0.1:9222")
    cdp._http_get = lambda url: (  # noqa: SLF001
        [target] if url.endswith("/json/list") else {"webSocketDebuggerUrl": "ws://b"}
    )
    from chat_harvest.test_readonly_cdp import FakeSocket

    collect_prefix = "(" + COLLECT_SCRIPT.text
    req = "https://claude.ai/chat/x"
    cases = [
        (req, True),
        ("https://claude.ai/chat/other", False),
        ({}, None),
    ]

    for page_url, want in cases:
        cdp.frames.clear()

        class PageSock(FakeSocket):
            def recv(self) -> str:
                expr = self.frames[-1]["params"]["expression"]
                mid = self.frames[-1]["id"]
                if expr.startswith(collect_prefix):
                    page = (
                        {"url": {"page_text": page_url, "chars": len(page_url), "cut": False}}
                        if isinstance(page_url, str)
                        else {}
                    )
                    val = {"nodes": [], "counts": {"total": 0, "returned": 0}, "page": page, "containers": []}
                else:
                    val = {"turn_nodes": 1, "element_count": 1, "login_wall": False}
                return json.dumps({"id": mid, "result": {"result": {"value": val}}})

        set_open_connection(lambda *_a, **_k: PageSock())
        monkeypatch.setattr("chat_harvest.dom_observation_poll.time.sleep", lambda _s: None)
        out = run_dom_observation(
            chat_url=req,
            conversation_id="x",
            cdp_url="http://127.0.0.1:9222",
            match_terms=["a"],
            max_nodes=20,
            open_on_demand=False,
            scope="conversation:x",
            transport=cdp,
        )
        assert out["observation"]["page"]["url_matches_request"] is want


def test_readiness_waited_ms_includes_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    target = {
        "type": "page",
        "id": "p1",
        "url": "https://claude.ai/chat/x",
        "webSocketDebuggerUrl": "ws://page",
    }
    cdp = ReadonlyCDP("http://127.0.0.1:9222")
    cdp._http_get = lambda url: (  # noqa: SLF001
        [target] if url.endswith("/json/list") else {"webSocketDebuggerUrl": "ws://b"}
    )
    from chat_harvest.test_readonly_cdp import FakeSocket

    sleeps: list[float] = []

    def _sleep(secs: float) -> None:
        sleeps.append(secs)

    collect_prefix = "(" + COLLECT_SCRIPT.text

    class PageSock(FakeSocket):
        def recv(self) -> str:
            expr = self.frames[-1]["params"]["expression"]
            mid = self.frames[-1]["id"]
            if expr.startswith(collect_prefix):
                val = {"nodes": [], "counts": {"total": 0, "returned": 0}, "page": {}, "containers": []}
            else:
                val = {"turn_nodes": 1, "element_count": 1, "login_wall": False}
            return json.dumps({"id": mid, "result": {"result": {"value": val}}})

    set_open_connection(lambda *_a, **_k: PageSock())
    monkeypatch.setattr("chat_harvest.dom_observation_poll.time.sleep", _sleep)
    out = run_dom_observation(
        chat_url="https://claude.ai/chat/x",
        conversation_id="x",
        cdp_url="http://127.0.0.1:9222",
        match_terms=["a"],
        max_nodes=20,
        open_on_demand=False,
        scope="conversation:x",
        transport=cdp,
        monotonic=lambda: 0.0,
    )
    want = int(sum(sleeps) * 1000)
    assert out["observation"]["readiness"]["waited_ms"] == want
    assert want > 0


def test_close_success_false_stamps_minted_tab_closed_false(monkeypatch: pytest.MonkeyPatch) -> None:
    minted_id = "mint-close-fail"
    chat_url = "https://claude.ai/chat/close-fail"
    state = {"minted": False}
    cdp = ReadonlyCDP("http://127.0.0.1:9222")

    def _http_get(url: str) -> Any:
        if url.endswith("/json/version"):
            return {"webSocketDebuggerUrl": "ws://browser"}
        if url.endswith("/json/list"):
            if state["minted"]:
                return [
                    {
                        "type": "page",
                        "id": minted_id,
                        "url": chat_url,
                        "webSocketDebuggerUrl": "ws://page",
                    }
                ]
            return []
        raise AssertionError(url)

    cdp._http_get = _http_get  # noqa: SLF001
    from chat_harvest.test_readonly_cdp import FakeSocket

    collect_prefix = "(" + COLLECT_SCRIPT.text

    def _open(ws: str, **_kwargs: Any) -> FakeSocket:
        if "browser" in ws:
            sock = FakeSocket()

            def _recv() -> str:
                mid = sock.frames[-1]["id"]
                method = sock.frames[-1]["method"]
                if method == "Target.createTarget":
                    state["minted"] = True
                    return json.dumps({"id": mid, "result": {"targetId": minted_id}})
                if method == "Target.closeTarget":
                    return json.dumps({"id": mid, "result": {"success": False}})
                return json.dumps({"id": mid, "result": {}})

            sock.recv = _recv  # type: ignore[method-assign]
            return sock

        class PageSock(FakeSocket):
            def recv(self) -> str:
                expr = self.frames[-1]["params"]["expression"]
                mid = self.frames[-1]["id"]
                if expr.startswith(collect_prefix):
                    val = {"nodes": [], "counts": {}, "page": {}, "containers": []}
                else:
                    val = {"turn_nodes": 1, "element_count": 1, "login_wall": False}
                return json.dumps({"id": mid, "result": {"result": {"value": val}}})

        return PageSock()

    set_open_connection(_open)
    monkeypatch.setattr("chat_harvest.dom_observation_poll.time.sleep", lambda _s: None)
    out = run_dom_observation(
        chat_url=chat_url,
        conversation_id="close-fail",
        cdp_url="http://127.0.0.1:9222",
        match_terms=["a"],
        max_nodes=20,
        open_on_demand=True,
        scope="conversation:close-fail",
        transport=cdp,
    )
    assert out["observation"]["tab"]["minted_tab_closed"] is False


def test_failure_detail_on_transport_and_collect_errors() -> None:
    cdp = ReadonlyCDP("http://127.0.0.1:9222")
    cdp._http_get = lambda url: (_ for _ in ()).throw(OSError("list down"))  # noqa: ARG005
    out = run_dom_observation(
        chat_url="https://claude.ai/chat/x",
        conversation_id="x",
        cdp_url="http://127.0.0.1:9222",
        match_terms=["a"],
        max_nodes=20,
        open_on_demand=True,
        scope="conversation:x",
        transport=cdp,
    )
    assert out["observation"]["failure_detail"] == "OSError"
    assert out["code"] == "unreachable"

    target = {
        "type": "page",
        "id": "p1",
        "url": "https://claude.ai/chat/x",
        "webSocketDebuggerUrl": "ws://page",
    }
    cdp2 = ReadonlyCDP("http://127.0.0.1:9222")
    cdp2._http_get = lambda url: (  # noqa: SLF001
        [target] if url.endswith("/json/list") else {"webSocketDebuggerUrl": "ws://b"}
    )
    from chat_harvest.test_readonly_cdp import FakeSocket

    set_open_connection(lambda *_a, **_k: FakeSocket(exception_details=True))
    out2 = run_dom_observation(
        chat_url="https://claude.ai/chat/x",
        conversation_id="x",
        cdp_url="http://127.0.0.1:9222",
        match_terms=["a"],
        max_nodes=20,
        open_on_demand=False,
        scope="conversation:x",
        transport=cdp2,
    )
    assert out2["observation"]["failure_detail"] == "CollectorScriptError"


def test_script_config_attr_elements_cap() -> None:
    assert script_config(["x"], 20)["caps"]["attr_elements_scanned"] == 20000


def test_collector_scripts_parse_as_expressions() -> None:
    import shutil
    import subprocess

    node = shutil.which("node")
    root = Path(__file__).resolve().parent
    for name in ("dom_observation_ready.js", "dom_observation_collect.js"):
        text = (root / name).read_text(encoding="utf-8").strip()
        wrapped = f"({text})"
        if node is None:
            pytest.skip("node not on PATH")
        proc = subprocess.run([node, "-e", wrapped], capture_output=True, text=True, check=False)
        assert proc.returncode == 0, proc.stderr
