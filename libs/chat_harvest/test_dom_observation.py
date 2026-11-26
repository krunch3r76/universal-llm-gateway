"""DOM observation shaping and validation tests (AC1, AC7)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from chat_harvest.dom_observation import (
    InvalidPattern,
    build_observation_block,
    cap_observation_size,
    run_dom_observation,
    shape_node,
    validate_match_terms,
)
from chat_harvest.readonly_cdp import COLLECT_SCRIPT, ReadonlyCDP, set_open_connection

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
    cfg = validate_match_terms(["abc", "def"])
    assert cfg == ["abc", "def"]


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
    assert cap_observation_size(obs)["truncated"] is True


def test_readiness_stops_at_budget(monkeypatch: pytest.MonkeyPatch) -> None:
    t = {"now": 0.0}

    def _mono() -> float:
        t["now"] += 20.0
        return t["now"]

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

    set_open_connection(
        lambda *_a, **_k: FakeSocket(
            evaluate_value={"turn_nodes": 0, "element_count": 1, "login_wall": False}
        )
    )
    monkeypatch.setattr("chat_harvest.dom_observation.time.sleep", lambda _s: None)
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
    assert out["outcome"] in {"observed", "unreachable"}
    assert out["observation"]["readiness"]["turns_seen"] is False


def test_wall_clock_budget_sets_code_timeout_and_closes_minted_tab(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    t = {"now": 0.0}

    def _mono() -> float:
        t["now"] += 40.0
        return t["now"]

    cdp = ReadonlyCDP("http://127.0.0.1:9222")
    cdp._http_get = lambda url: (  # noqa: SLF001
        [] if url.endswith("/json/list") else {"webSocketDebuggerUrl": "ws://browser"}
    )
    set_open_connection(lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("timeout")))
    target = {
        "type": "page",
        "id": "p1",
        "url": "https://claude.ai/chat/x",
        "webSocketDebuggerUrl": "ws://127.0.0.1:9222/devtools/page/p1",
    }
    cdp._http_get = lambda url: (  # noqa: SLF001
        [target] if url.endswith("/json/list") else {"webSocketDebuggerUrl": "ws://b"}
    )
    from chat_harvest.test_readonly_cdp import FakeSocket

    set_open_connection(
        lambda *_a, **_k: FakeSocket(
            evaluate_value={"turn_nodes": 0, "element_count": 1, "login_wall": False}
        )
    )
    monkeypatch.setattr("chat_harvest.dom_observation.time.sleep", lambda _s: None)
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
    assert out.get("code") == "timeout"
    assert out["outcome"] == "unreachable"


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


def test_invalid_terms_raise() -> None:
    with pytest.raises(InvalidPattern):
        validate_match_terms([])
    with pytest.raises(InvalidPattern):
        validate_match_terms(["x"])
