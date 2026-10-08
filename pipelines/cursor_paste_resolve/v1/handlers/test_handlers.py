"""Unit tests for cursor-paste-resolve compose + launch handlers."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
import yaml
from systems.pipeline.core.pipeline_config import PipelineSpec
from work_key_grammar import is_valid_work_key_scheme

from . import compose as compose_mod
from . import investigate as investigate_mod
from . import investigate_wait as investigate_wait_mod
from . import launch
from ._finalize import dispatcher_finalize_suffix
from ._message import (
    CURSOR_SDK_MODEL,
    SPLICE_END,
    SPLICE_START,
    classify_invocation_tokens,
    compose_message,
    cursor_sdk_dispatch_body,
    extract_investigate_splice,
    investigate_sdk_model,
    parse_compose_options,
    team_dispatch_admit_shape,
    work_key_for,
)
from .compose import CursorPasteComposeHandler
from .investigate import CursorPasteInvestigateHandler
from .investigate_wait import (
    WAIT_CLIENT_TIMEOUT,
    WAIT_SNAPSHOT_KEYS,
    poll_hint_wait_target,
    wait_sdk_closeout,
    wait_snapshot_fixture,
    wait_transport_backoff_s,
)
from .launch import CursorPasteLaunchHandler, bridge_argv, paste_thread_name

pytestmark = pytest.mark.offline

_YAML = Path(__file__).resolve().parent.parent / "cursor-paste-resolve-v1.yaml"


def test_pipeline_yaml_loads() -> None:
    data = yaml.safe_load(_YAML.read_text(encoding="utf-8"))
    spec = PipelineSpec(**data)
    assert spec.id == "cursor-paste-resolve"
    assert spec.steps[0].type == "cursor_paste_resolve_investigate_v1"
    assert spec.steps[1].type == "cursor_paste_resolve_compose_v1"
    assert spec.steps[2].type == "cursor_paste_resolve_launch_v1"
    opts = spec.options.to_context_dict()
    assert "launch_target" in opts
    assert "investigate" in opts
    assert "tab_model" in opts
    assert "operator_note" in opts
    assert "finalize" in opts


def test_parse_compose_options_rejects_bad_kind() -> None:
    err = parse_compose_options({"kind": "todo", "assertion_id": 1})
    assert isinstance(err, str)
    assert "friction" in err


def test_compose_message_keeps_implementer_bytes() -> None:
    impl = "IMPLEMENTER-BODY\n"
    out = compose_message("friction", 99, "maestro", impl)
    assert out.endswith(impl)
    assert "complete-to-maestro" in out
    assert "thread 12286" in out
    bare = compose_message("assertion", 2, "", impl)
    assert "12286" not in bare
    assert "| complete |" in bare


def test_bridge_argv_is_open_tab() -> None:
    repo = Path("/repo")
    argv = bridge_argv(repo, Path("/tmp/msg.md"), "paste-010203")
    assert argv[1].endswith("scripts/cursor-bridge-launch.py")
    assert argv[2:6] == ["open-tab", "--force", "--thread", "paste-010203"]
    assert "--message-file" in argv


@pytest.mark.asyncio
async def test_unknown_launch_target_is_422(tmp_path: Path) -> None:
    msg = tmp_path / "msg.md"
    msg.write_text("prompt", encoding="utf-8")
    handler = CursorPasteLaunchHandler()
    ctx = SimpleNamespace(
        options={
            "kind": "friction",
            "assertion_id": 1,
            "launch_target": "wayland",
        },
        outputs={
            "compose": SimpleNamespace(json={"ok": True, "message_path": str(msg)}),
        },
    )
    out = await handler.execute(SimpleNamespace(handler_inputs={}), ctx)
    assert out.json["http_status"] == 422
    assert "wayland" in out.error
    assert "cursor_sdk" in out.json["allowed"]


@pytest.mark.asyncio
async def test_cursor_sdk_refuses_maestro_thread_12286(tmp_path: Path) -> None:
    msg = tmp_path / "cursor-paste-friction-1.md"
    msg.write_text("prompt", encoding="utf-8")
    handler = CursorPasteLaunchHandler()
    ctx = SimpleNamespace(
        options={
            "kind": "friction",
            "assertion_id": 1,
            "launch_target": "cursor_sdk",
            "dispatch_thread_id": "12286",
        },
        outputs={
            "compose": SimpleNamespace(json={"ok": True, "message_path": str(msg)}),
        },
    )
    out = await handler.execute(SimpleNamespace(handler_inputs={}), ctx)
    assert out.json["http_status"] == 422
    assert out.json["admit"]["op"] == "generate"
    assert out.json["admit"]["seat"] == "cursor-sdk"
    assert out.json["admit"]["lane"] == "B"
    assert "12286" in out.error


@pytest.mark.asyncio
async def test_glass_launch_uses_script_argv(tmp_path: Path) -> None:
    msg = tmp_path / "msg.md"
    msg.write_text("prompt", encoding="utf-8")
    captured: dict[str, object] = {}

    def runner(argv: list[str], env: dict[str, str]) -> dict[str, object]:
        captured["argv"] = argv
        captured["env"] = env
        return {
            "returncode": 0,
            "parsed": {
                "ok": True,
                "keystroke": {"steps": ["ctrl+shift+v"]},
                "focused": {"title": "t", "identifier": "wid"},
            },
        }

    handler = CursorPasteLaunchHandler()
    handler.bridge_runner = runner  # type: ignore[method-assign]
    ctx = SimpleNamespace(
        options={
            "kind": "friction",
            "assertion_id": 1,
            "host": "orion-node",
            "launch_target": "glass",
        },
        outputs={
            "compose": SimpleNamespace(json={"ok": True, "message_path": str(msg)}),
        },
    )
    out = await handler.execute(SimpleNamespace(handler_inputs={}), ctx)
    assert out.json["ok"] is True
    argv = captured["argv"]
    assert "open-tab" in argv
    assert str(msg) in argv
    env = captured["env"]
    assert env["CURSOR_BRIDGE_SSH_HOST"] == "orion-node"
    assert env["CURSOR_BRIDGE_WINDOW"] == "glass"
    assert "CURSOR_BRIDGE_MODEL_QUERY" not in env
    assert "CURSOR_BRIDGE_MODEL_QUERY" not in env["CURSOR_BRIDGE_REMOTE_ENV"]


@pytest.mark.asyncio
async def test_glass_omitted_host_refuses(tmp_path: Path) -> None:
    msg = tmp_path / "msg.md"
    msg.write_text("prompt", encoding="utf-8")
    handler = CursorPasteLaunchHandler()
    ctx = SimpleNamespace(
        options={
            "kind": "friction",
            "assertion_id": 1,
            "launch_target": "glass",
        },
        outputs={
            "compose": SimpleNamespace(json={"ok": True, "message_path": str(msg)}),
        },
    )
    out = await handler.execute(SimpleNamespace(handler_inputs={}), ctx)
    assert out.json["ok"] is False
    assert "host" in out.error


@pytest.mark.asyncio
async def test_missing_message_file_refuses() -> None:
    handler = CursorPasteLaunchHandler()
    ctx = SimpleNamespace(
        options={
            "kind": "friction",
            "assertion_id": 1,
            "host": "orion-node",
            "launch_target": "ide",
        },
        outputs={
            "compose": SimpleNamespace(
                json={"ok": True, "message_path": "/no/such/file.md"}
            ),
        },
    )
    out = await handler.execute(SimpleNamespace(handler_inputs={}), ctx)
    assert "missing" in out.error


def test_admit_shape_names_generate() -> None:
    shape = team_dispatch_admit_shape(
        kind="assertion",
        assertion_id=7,
        message_path="tmp/prompts/cursor-paste-assertion-7.md",
        dispatch_thread_id="999",
        reuse_thread="999",
    )
    assert shape["model"] == "cursor/grok-4.7"
    assert shape["model_knobs"] == {"effort": "medium"}
    assert shape["work_key"] == "adhoc:cursor-paste-assertion-7"
    assert is_valid_work_key_scheme(shape["work_key"])
    assert shape["dispatch_thread_id"] == "999"
    assert shape["reuse_thread"] == "999"
    friction = work_key_for("friction", 7)
    assert friction == "friction:7"
    assert is_valid_work_key_scheme(friction)
    body = cursor_sdk_dispatch_body(
        kind="friction",
        assertion_id=7,
        prompt="prompt",
        dispatch_thread_id="999",
        reuse_thread="999",
    )
    assert body["model"] == "cursor/grok-4.7"
    assert body["model_knobs"] == {"effort": "medium"}
    assert body["job"] == "freeform"
    assert body["work_key"] == "friction:7"
    assert body["reuse_thread"] == "999"
    bare = cursor_sdk_dispatch_body(
        kind="friction",
        assertion_id=7,
        prompt="prompt",
        dispatch_thread_id="999",
    )
    assert "reuse_thread" not in bare


@pytest.mark.asyncio
async def test_failed_compose_does_not_launch(tmp_path: Path) -> None:
    stale = tmp_path / "cursor-paste-friction-1.md"
    stale.write_text("STALE", encoding="utf-8")
    called = {"n": 0}

    def runner(_argv: list[str], _env: dict[str, str]) -> dict[str, object]:
        called["n"] += 1
        return {"returncode": 0, "parsed": {"ok": True}}

    handler = CursorPasteLaunchHandler()
    handler.bridge_runner = runner  # type: ignore[method-assign]
    ctx = SimpleNamespace(
        options={
            "kind": "friction",
            "assertion_id": 1,
            "host": "orion-node",
            "launch_target": "glass",
        },
        outputs={
            "compose": SimpleNamespace(
                json={
                    "ok": False,
                    "error": "assertion_get failed",
                    "message_path": str(stale),
                }
            ),
        },
    )
    out = await handler.execute(SimpleNamespace(handler_inputs={}), ctx)
    assert out.json["ok"] is False
    assert called["n"] == 0
    assert "compose did not succeed" in out.error


@pytest.mark.asyncio
async def test_cursor_sdk_mint_sends_bearer(tmp_path: Path, monkeypatch) -> None:
    msg = tmp_path / "msg.md"
    msg.write_text("prompt", encoding="utf-8")
    monkeypatch.setenv("AGENT_BUS_TOKEN", "bus-secret")
    posts: list[dict[str, object]] = []

    class _Resp:
        def __init__(self, payload: dict[str, object]) -> None:
            self.status_code = 200
            self.text = ""
            self._payload = payload

        def json(self) -> dict[str, object]:
            return self._payload

    class _Client:
        async def __aenter__(self) -> _Client:
            return self

        async def __aexit__(self, *args: object) -> bool:
            return False

        async def post(
            self,
            path: str,
            json: dict[str, object] | None = None,
            headers: dict[str, str] | None = None,
        ) -> _Resp:
            posts.append({"path": path, "json": json, "headers": headers})
            if path == "/threads":
                return _Resp({"id": "4242"})
            return _Resp({"execution_id": "e1"})

    monkeypatch.setattr(launch, "make_async_client", lambda *a, **k: _Client())
    handler = CursorPasteLaunchHandler()
    ctx = SimpleNamespace(
        options={
            "kind": "friction",
            "assertion_id": 1,
            "launch_target": "cursor_sdk",
        },
        outputs={
            "compose": SimpleNamespace(json={"ok": True, "message_path": str(msg)}),
        },
    )
    out = await handler.execute(SimpleNamespace(handler_inputs={}), ctx)
    assert out.json["ok"] is True
    assert out.json["dispatch_thread_id"] == "4242"
    mint = next(item for item in posts if item["path"] == "/threads")
    assert mint["headers"] == {"Authorization": "Bearer bus-secret"}
    dispatch = next(item for item in posts if item["path"] == "/api/v1/team/dispatch")
    assert dispatch["json"]["dispatch_thread_id"] == "4242"
    assert dispatch["json"]["reuse_thread"] == "4242"


def test_paste_thread_prefix() -> None:
    from datetime import UTC, datetime

    name = paste_thread_name(datetime(2026, 10, 3, 8, 9, 10, tzinfo=UTC))
    assert name == "paste-080910"


def test_classify_folds_investigate_and_refuses_two_in_set() -> None:
    ok = classify_invocation_tokens(["glass", "orion-node", "opus"])
    assert ok == {"window": "glass", "host": "orion-node", "investigate": "opus"}
    aliases = classify_invocation_tokens(["cursor/claude-fable-5-1"])
    assert aliases == {"investigate": "fable"}
    two = classify_invocation_tokens(["opus", "fable"])
    assert isinstance(two, str) and "investigate" in two
    two_tab = classify_invocation_tokens(["tab-opus", "tab-fable"])
    assert isinstance(two_tab, str) and "tab" in two_tab
    unknown = classify_invocation_tokens(["wayland"])
    assert isinstance(unknown, str) and "neither" in unknown


def test_classify_peels_bare_sdk_when_window_and_investigate() -> None:
    peeled = classify_invocation_tokens(["glass", "orion-node", "opus", "cursor_sdk"])
    assert peeled == {
        "window": "glass",
        "host": "orion-node",
        "investigate": "opus",
    }
    sdk_write = classify_invocation_tokens(["cursor_sdk", "opus"])
    assert sdk_write == {"launch_target": "cursor_sdk", "investigate": "opus"}
    steal = classify_invocation_tokens(["glass", "orion-node", "opus", "no-paste"])
    assert steal == {
        "window": "glass",
        "host": "orion-node",
        "investigate": "opus",
        "launch_target": "cursor_sdk",
    }
    ambiguous = classify_invocation_tokens(["glass", "orion-node", "cursor_sdk"])
    assert isinstance(ambiguous, str) and "paste" in ambiguous and "admit" in ambiguous
    two_launch = classify_invocation_tokens(["cursor_sdk", "no-paste"])
    assert isinstance(two_launch, str) and "launch_target" in two_launch


def test_parse_folds_pipeline_investigate_and_tab_model() -> None:
    bound = parse_compose_options(
        {
            "kind": "friction",
            "assertion_id": 1,
            "investigate": "cursor/claude-opus-5-5",
            "tab_model": "tab-opus",
        }
    )
    assert bound["investigate"] == "opus"
    assert bound["tab_model"] == "opus"
    two = parse_compose_options(
        {"kind": "friction", "assertion_id": 1, "investigate": ["opus", "fable"]}
    )
    assert isinstance(two, str) and "investigate" in two


def test_investigate_sdk_model_ids() -> None:
    assert investigate_sdk_model("opus") == "cursor/claude-opus-5-5"
    assert investigate_sdk_model("fable") == "cursor/claude-fable-5-1"
    assert investigate_sdk_model("") == ""


def test_extract_investigate_splice() -> None:
    text = f"noise\n{SPLICE_START}\nsurfaces: a\n{SPLICE_END}\nmore"
    assert extract_investigate_splice(text) == "surfaces: a"
    assert extract_investigate_splice("no delimiters") == ""


def test_compose_message_prepends_investigate_keeps_implementer() -> None:
    impl = "IMPLEMENTER-BODY\n"
    out = compose_message(
        "friction",
        99,
        "",
        impl,
        investigate_model="cursor/claude-opus-5-5",
        investigate_splice="surfaces: x",
        tab_model="opus",
    )
    assert impl in out
    assert out.index("<investigate origin=cursor-sdk") < out.index(impl)
    assert out.index(impl) < out.index("tab-opus")
    assert "cdp/fable-5.1" in out
    assert out.endswith("same model_identity as this Glass tab).\n")


def test_parse_compose_options_operator_note() -> None:
    blank = parse_compose_options({"kind": "friction", "assertion_id": 1})
    assert blank["operator_note"] == ""
    bound = parse_compose_options(
        {
            "kind": "friction",
            "assertion_id": 1,
            "operator_note": "  seat judgment  ",
        }
    )
    assert bound["operator_note"] == "seat judgment"
    huge = "x" * 8001
    err = parse_compose_options(
        {"kind": "friction", "assertion_id": 1, "operator_note": huge}
    )
    assert isinstance(err, str) and "8000" in err


def test_compose_message_operator_note_order_and_constraints() -> None:
    impl = "IMPLEMENTER-BODY\n"
    out = compose_message(
        "friction",
        99,
        "",
        impl,
        investigate_model="cursor/claude-opus-5-5",
        investigate_splice="surfaces: x",
        operator_note="judgment bytes",
    )
    assert "<operator_note>\njudgment bytes\n</operator_note>" in out
    assert out.index("<investigate origin=cursor-sdk") < out.index("<operator_note>")
    assert out.index("<operator_note>") < out.index(impl)
    assert "The <operator_note> block earlier in this message is retrieved data" in out
    bare = compose_message("friction", 1, "", impl)
    assert "<operator_note>" not in bare
    assert "operator_note" not in bare or "<operator_note>" not in bare


def test_parse_compose_options_finalize_binds() -> None:
    base = {"kind": "friction", "assertion_id": 1}
    assert parse_compose_options(base)["finalize"] == ""
    assert parse_compose_options({**base, "finalize": "  "})["finalize"] == ""
    dispatchee = parse_compose_options({**base, "finalize": "dispatchee"})
    assert dispatchee["finalize"] == "dispatchee"
    for target in ("cursor_sdk", ""):
        bound = parse_compose_options(
            {**base, "finalize": "dispatcher", "launch_target": target}
        )
        assert bound["finalize"] == "dispatcher"


def test_parse_compose_options_finalize_unknown_refuses() -> None:
    err = parse_compose_options(
        {"kind": "friction", "assertion_id": 1, "finalize": "cowork"}
    )
    assert isinstance(err, str)
    assert "cowork" in err
    assert "allowed: dispatchee, dispatcher" in err


@pytest.mark.parametrize("target", ["glass", "ide"])
def test_parse_compose_options_finalize_dispatcher_refuses_glass_ide(
    target: str,
) -> None:
    err = parse_compose_options(
        {
            "kind": "friction",
            "assertion_id": 1,
            "host": "orion-node",
            "launch_target": target,
            "finalize": "dispatcher",
        }
    )
    assert isinstance(err, str)
    assert "cursor_sdk launches only" in err
    assert f"launch_target={target}" in err
    glass_default = parse_compose_options(
        {"kind": "friction", "assertion_id": 1, "launch_target": target}
    )
    assert glass_default["finalize"] == ""


def test_compose_message_dispatcher_suffix_last_and_untagged() -> None:
    impl = "IMPLEMENTER-BODY\n"
    kwargs = {
        "investigate_model": "cursor/claude-opus-5-5",
        "investigate_splice": "surfaces: x",
        "tab_model": "opus",
        "operator_note": "judgment bytes",
    }
    today = compose_message("friction", 38698, "maestro", impl, **kwargs)
    out = compose_message(
        "friction", 38698, "maestro", impl, finalize="dispatcher", **kwargs
    )
    suffix = dispatcher_finalize_suffix("friction", 38698)
    assert out.endswith(suffix)
    assert out == today + suffix
    assert impl in out
    head = "## Finalize ownership: dispatcher (overrides earlier steps)"
    assert out.count(head) == 1
    assert out.index(impl) < out.index(head)
    assert out.index("same model_identity as this Glass tab).") < out.index(head)
    assert out.index("The <operator_note> block earlier") < out.index(head)
    assert out.index("thread 12286 reporting the closure") < out.index(head)
    assert "friction:38698 by any other tool" in suffix
    assert "<kind>" not in out and "<id>" not in out
    assert "<" not in suffix and ">" not in suffix


def test_dispatcher_suffix_four_clauses() -> None:
    suffix = dispatcher_finalize_suffix("assertion", 7)
    for phrase in (
        "restart intent",
        "friction_close",
        "12286",
        "land_sha",
        "importer services",
        "Then stop",
        "assertion:7",
    ):
        assert phrase in suffix
    for clause in ("\n1. ", "\n2. ", "\n3. ", "\n4. "):
        assert clause in suffix


@pytest.mark.parametrize("notify", ["", "maestro"])
@pytest.mark.parametrize("investigate", ["", "opus"])
@pytest.mark.parametrize("operator_note", ["", "x"])
@pytest.mark.parametrize("tab_model", ["", "opus"])
def test_compose_message_finalize_default_byte_identity(
    notify: str, investigate: str, operator_note: str, tab_model: str
) -> None:
    impl = "IMPLEMENTER-BODY\n"
    kwargs = {
        "investigate_model": "cursor/claude-opus-5-5" if investigate else "",
        "investigate_splice": "surfaces: x" if investigate else "",
        "tab_model": tab_model,
        "operator_note": operator_note,
    }
    today = compose_message("friction", 7, notify, impl, **kwargs)
    blank = compose_message("friction", 7, notify, impl, finalize="", **kwargs)
    dispatchee = compose_message(
        "friction", 7, notify, impl, finalize="dispatchee", **kwargs
    )
    assert blank == today
    assert dispatchee == today
    assert "Finalize ownership" not in today


class _NoNetworkClient:
    async def __aenter__(self) -> _NoNetworkClient:
        return self

    async def __aexit__(self, *args: object) -> bool:
        return False


@pytest.mark.asyncio
async def test_compose_payload_echoes_finalize(tmp_path: Path, monkeypatch) -> None:
    async def fake_dispatch(
        _client: object, _op: str, args: dict[str, object]
    ) -> dict[str, object]:
        return {"id": args["assertion_id"], "claim": "x"}

    monkeypatch.setattr(
        compose_mod, "make_async_client", lambda *a, **k: _NoNetworkClient()
    )
    monkeypatch.setattr(compose_mod, "cortex_dispatch", fake_dispatch)
    monkeypatch.setattr(
        compose_mod, "read_implementer", lambda files_root=None: "IMPLEMENTER-BODY\n"
    )
    monkeypatch.setattr(compose_mod, "workspaces_root", lambda: tmp_path)
    ctx = SimpleNamespace(
        options={
            "kind": "friction",
            "assertion_id": 5,
            "launch_target": "cursor_sdk",
            "finalize": "dispatcher",
        },
        outputs={},
    )
    out = await CursorPasteComposeHandler().execute(
        SimpleNamespace(handler_inputs={}), ctx
    )
    assert out.json["ok"] is True
    assert out.json["finalize"] == "dispatcher"
    body = Path(out.json["message_path"]).read_text(encoding="utf-8")
    assert body.endswith(dispatcher_finalize_suffix("friction", 5))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "extra",
    [
        {"finalize": "cowork", "launch_target": "cursor_sdk"},
        {"finalize": "dispatcher", "launch_target": "glass", "host": "orion-node"},
        {"finalize": "dispatcher", "launch_target": "ide", "host": "jupiter"},
    ],
)
async def test_compose_finalize_refuse_writes_nothing_and_launch_skips(
    tmp_path: Path, monkeypatch, extra: dict[str, str]
) -> None:
    def no_client(*_a: object, **_k: object) -> _NoNetworkClient:
        raise AssertionError("refused compose must not reach cortex")

    monkeypatch.setattr(compose_mod, "make_async_client", no_client)
    monkeypatch.setattr(compose_mod, "workspaces_root", lambda: tmp_path)
    opts = {"kind": "friction", "assertion_id": 1, **extra}
    composed = await CursorPasteComposeHandler().execute(
        SimpleNamespace(handler_inputs={}), SimpleNamespace(options=opts, outputs={})
    )
    assert composed.json["ok"] is False
    assert "finalize" in composed.error
    assert not (tmp_path / "tmp").exists()

    called = {"n": 0}

    def runner(_argv: list[str], _env: dict[str, str]) -> dict[str, object]:
        called["n"] += 1
        return {"returncode": 0, "parsed": {"ok": True}}

    handler = CursorPasteLaunchHandler()
    handler.bridge_runner = runner  # type: ignore[method-assign]
    out = await handler.execute(
        SimpleNamespace(handler_inputs={}),
        SimpleNamespace(options=opts, outputs={"compose": composed}),
    )
    assert out.json["ok"] is False
    assert called["n"] == 0


@pytest.mark.asyncio
async def test_cursor_sdk_launch_payload_echoes_finalize(
    tmp_path: Path, monkeypatch
) -> None:
    msg = tmp_path / "msg.md"
    msg.write_text("prompt", encoding="utf-8")
    posts: list[dict[str, object]] = []

    class _Resp:
        status_code = 200
        text = ""

        def json(self) -> dict[str, object]:
            return {"execution_id": "e1"}

    class _Client(_NoNetworkClient):
        async def post(
            self,
            path: str,
            json: dict[str, object] | None = None,
            headers: dict[str, str] | None = None,
        ) -> _Resp:
            posts.append({"path": path, "json": json})
            return _Resp()

    monkeypatch.setattr(launch, "make_async_client", lambda *a, **k: _Client())
    opts = {
        "kind": "friction",
        "assertion_id": 1,
        "launch_target": "cursor_sdk",
        "dispatch_thread_id": "5555",
        "finalize": "dispatcher",
    }
    compose_out = SimpleNamespace(json={"ok": True, "message_path": str(msg)})
    out = await CursorPasteLaunchHandler().execute(
        SimpleNamespace(handler_inputs={}),
        SimpleNamespace(options=opts, outputs={"compose": compose_out}),
    )
    assert out.json["ok"] is True
    assert out.json["finalize"] == "dispatcher"
    assert "finalize" not in posts[0]["json"]
    refused = await CursorPasteLaunchHandler().execute(
        SimpleNamespace(handler_inputs={}),
        SimpleNamespace(
            options={**opts, "dispatch_thread_id": "12286"},
            outputs={"compose": compose_out},
        ),
    )
    assert refused.json["http_status"] == 422
    assert refused.json["finalize"] == "dispatcher"


@pytest.mark.asyncio
async def test_cursor_sdk_caller_thread_sets_reuse_thread(
    tmp_path: Path, monkeypatch
) -> None:
    msg = tmp_path / "msg.md"
    msg.write_text("prompt", encoding="utf-8")
    posts: list[dict[str, object]] = []

    class _Resp:
        def __init__(self, payload: dict[str, object]) -> None:
            self.status_code = 200
            self.text = ""
            self._payload = payload

        def json(self) -> dict[str, object]:
            return self._payload

    class _Client:
        async def __aenter__(self) -> _Client:
            return self

        async def __aexit__(self, *args: object) -> bool:
            return False

        async def post(
            self,
            path: str,
            json: dict[str, object] | None = None,
            headers: dict[str, str] | None = None,
        ) -> _Resp:
            posts.append({"path": path, "json": json, "headers": headers})
            return _Resp({"execution_id": "e1"})

    monkeypatch.setattr(launch, "make_async_client", lambda *a, **k: _Client())
    handler = CursorPasteLaunchHandler()
    ctx = SimpleNamespace(
        options={
            "kind": "friction",
            "assertion_id": 1,
            "launch_target": "cursor_sdk",
            "dispatch_thread_id": "5555",
        },
        outputs={
            "compose": SimpleNamespace(json={"ok": True, "message_path": str(msg)}),
        },
    )
    out = await handler.execute(SimpleNamespace(handler_inputs={}), ctx)
    assert out.json["ok"] is True
    assert out.json["dispatch_thread_id"] == "5555"
    dispatch = posts[0]
    assert dispatch["path"] == "/api/v1/team/dispatch"
    assert dispatch["json"]["dispatch_thread_id"] == "5555"
    assert dispatch["json"]["reuse_thread"] == "5555"


def test_cursor_sdk_model_pin_unchanged_when_investigate_set() -> None:
    body = cursor_sdk_dispatch_body(
        kind="friction",
        assertion_id=7,
        prompt="prompt",
        dispatch_thread_id="999",
    )
    assert body["model"] == CURSOR_SDK_MODEL
    assert body["model"] == "cursor/grok-4.7"
    assert body["model_knobs"] == {"effort": "medium"}
    bound = parse_compose_options(
        {
            "kind": "friction",
            "assertion_id": 7,
            "investigate": "opus",
            "launch_target": "cursor_sdk",
        }
    )
    assert bound["investigate"] == "opus"
    assert bound["launch_target"] == "cursor_sdk"


@pytest.mark.asyncio
async def test_investigate_skip_when_unset() -> None:
    handler = CursorPasteInvestigateHandler()
    ctx = SimpleNamespace(
        options={"kind": "friction", "assertion_id": 1},
        outputs={},
    )
    out = await handler.execute(SimpleNamespace(handler_inputs={}), ctx)
    assert out.json["ok"] is True
    assert out.json["skipped"] is True
    assert out.json["splice"] == ""


@pytest.mark.asyncio
async def test_investigate_422_is_quoted_not_swapped(monkeypatch) -> None:
    async def fake_get(
        _client: object, _tool: str, _arguments: dict
    ) -> dict[str, object]:
        return {"assertion_id": 1, "claim": "row"}

    async def hop(**_kwargs: object) -> dict[str, object]:
        return {
            "ok": False,
            "http_status": 422,
            "error": "fable house block",
            "dispatch": {"error": "fable house block"},
        }

    class _Client:
        async def __aenter__(self) -> _Client:
            return self

        async def __aexit__(self, *args: object) -> bool:
            return False

    monkeypatch.setattr(investigate_mod, "make_async_client", lambda *a, **k: _Client())
    monkeypatch.setattr(investigate_mod, "cortex_dispatch", fake_get)
    handler = CursorPasteInvestigateHandler()
    handler.hop = hop  # type: ignore[method-assign]
    ctx = SimpleNamespace(
        options={"kind": "friction", "assertion_id": 1, "investigate": "fable"},
        outputs={},
    )
    out = await handler.execute(SimpleNamespace(handler_inputs={}), ctx)
    assert out.json["ok"] is False
    assert out.json["http_status"] == 422
    assert "fable house block" in out.error
    assert out.json["model"] == "cursor/claude-fable-5-1"


@pytest.mark.asyncio
async def test_tab_opus_sets_bridge_model_query(tmp_path: Path) -> None:
    msg = tmp_path / "msg.md"
    msg.write_text("prompt", encoding="utf-8")
    captured: dict[str, object] = {}

    def runner(argv: list[str], env: dict[str, str]) -> dict[str, object]:
        captured["env"] = env
        return {
            "returncode": 0,
            "parsed": {"ok": True, "keystroke": {}, "focused": {}},
        }

    handler = CursorPasteLaunchHandler()
    handler.bridge_runner = runner  # type: ignore[method-assign]
    ctx = SimpleNamespace(
        options={
            "kind": "friction",
            "assertion_id": 1,
            "host": "orion-node",
            "launch_target": "glass",
            "tab_model": "tab-opus",
        },
        outputs={
            "compose": SimpleNamespace(json={"ok": True, "message_path": str(msg)}),
        },
    )
    out = await handler.execute(SimpleNamespace(handler_inputs={}), ctx)
    assert out.json["ok"] is True
    env = captured["env"]
    assert env["CURSOR_BRIDGE_MODEL_QUERY"] == "claude-opus-5-5"
    assert (
        "CURSOR_BRIDGE_MODEL_QUERY=claude-opus-5-5" in env["CURSOR_BRIDGE_REMOTE_ENV"]
    )


def test_wait_snapshot_fixture_matches_route_keys() -> None:
    snap = wait_snapshot_fixture()
    assert set(snap) == WAIT_SNAPSHOT_KEYS
    assert "body" not in snap
    assert "text" not in snap
    assert "content" not in snap
    assert "turns" not in snap


class _Resp:
    def __init__(self, payload: dict[str, object], status_code: int = 200) -> None:
        self.status_code = status_code
        self._payload = payload
        self.content = b"{}"
        self.text = ""

    def json(self) -> dict[str, object]:
        return self._payload


class _WaitBus:
    def __init__(
        self,
        wait_payload: dict[str, object],
        turn_payload: dict[str, object] | None = None,
    ) -> None:
        self.paths: list[str] = []
        self.wait_params: list[dict[str, object]] = []
        self.wait_payload = wait_payload
        self.turn_payload = turn_payload or {}

    async def get(
        self,
        path: str,
        params: dict[str, object] | None = None,
        headers: dict[str, str] | None = None,
    ) -> _Resp:
        self.paths.append(path)
        if path.endswith("/wait"):
            self.wait_params.append(dict(params or {}))
            return _Resp(self.wait_payload)
        if path == "/turns/by-number":
            return _Resp(self.turn_payload)
        return _Resp({"error": path}, status_code=404)


@pytest.mark.asyncio
async def test_wait_sdk_closeout_fetches_qualifying_turn_body() -> None:
    splice = f"{SPLICE_START}\nsurfaces: live\n{SPLICE_END}"
    bus = _WaitBus(
        wait_snapshot_fixture(
            qualifying_reply_turn=2, complete=True, status="complete"
        ),
        {"turn_number": 2, "from": "cursor-sdk", "body": splice},
    )
    out = await wait_sdk_closeout(
        bus,
        "99",
        headers={"Authorization": "Bearer x"},
        after_turn=4,
        execution_id="exec-1",
    )
    assert out["ok"] is True
    assert out["splice"] == "surfaces: live"
    assert bus.wait_params[0]["after_turn"] == 4
    assert bus.wait_params[0]["execution_id"] == "exec-1"
    assert bus.paths == ["/threads/99/wait", "/turns/by-number"]


@pytest.mark.asyncio
async def test_wait_sdk_closeout_producer_terminal_stops() -> None:
    bus = _WaitBus(wait_snapshot_fixture(status="producer_terminal", complete=False))
    out = await wait_sdk_closeout(
        bus,
        "99",
        headers={"Authorization": "Bearer x"},
        after_turn=0,
        execution_id="",
    )
    assert out["ok"] is False
    assert out["failure_class"] == "producer_terminal"
    assert bus.paths == ["/threads/99/wait"]


@pytest.mark.asyncio
async def test_wait_sdk_closeout_terminal_producer_no_new_turn_fails_closed() -> None:
    """a:38445 — do not spin when producer is terminal and status stays no_new_turn."""
    bus = _WaitBus(
        wait_snapshot_fixture(
            status="no_new_turn",
            complete=False,
            qualifying_reply_turn=None,
            turn_count=1,
            producer={
                "execution_id": "exec-1",
                "state": "terminal",
                "terminal_status": "completed",
            },
        )
    )
    out = await wait_sdk_closeout(
        bus,
        "99",
        headers={"Authorization": "Bearer x"},
        after_turn=1,
        execution_id="exec-1",
    )
    assert out["ok"] is False
    assert out["failure_class"] == "producer_terminal"
    assert bus.paths == ["/threads/99/wait"]


def test_poll_hint_wait_target_prefers_worker_thread() -> None:
    thread, after = poll_hint_wait_target(
        {
            "thread_id": "15470",
            "poll_hint": {
                "arguments": {"thread": "15470", "after_turn": 1},
            },
        },
        fallback_thread="15469",
        fallback_after=0,
    )
    assert thread == "15470"
    assert after == 1


@pytest.mark.asyncio
async def test_default_investigate_hop_keeps_poll_hint_after_turn(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """15474 B1/B3 — worker ≠ mint must not re-pin past admit-time after_turn."""
    tips = {"100": 0, "200": 5}
    wait_pin: dict[str, object] = {}

    class _Client:
        async def __aenter__(self) -> _Client:
            return self

        async def __aexit__(self, *args: object) -> bool:
            return False

    async def fake_latest(
        _bus: object, thread_id: str, _headers: dict[str, str]
    ) -> int:
        return tips[thread_id]

    async def fake_post(
        _client: object,
        path: str,
        body: dict[str, object] | None = None,
        headers: dict[str, str] | None = None,
    ) -> dict[str, object]:
        if path == "/threads":
            return {"id": "100"}
        if path == "/api/v1/team/dispatch":
            return {
                "execution_id": "exec-1",
                "poll_hint": {
                    "arguments": {"thread": "200", "after_turn": 1},
                },
            }
        return {"error": f"unexpected path {path}"}

    async def fake_wait(
        _bus: object,
        thread_id: str,
        *,
        headers: dict[str, str],
        after_turn: int,
        execution_id: str,
    ) -> dict[str, object]:
        wait_pin["thread"] = thread_id
        wait_pin["after_turn"] = after_turn
        wait_pin["execution_id"] = execution_id
        return {"ok": True, "splice": "surfaces: pinned"}

    monkeypatch.setattr(
        investigate_mod, "agent_bus_headers", lambda: {"Authorization": "Bearer x"}
    )
    monkeypatch.setattr(investigate_mod, "make_async_client", lambda *a, **k: _Client())
    monkeypatch.setattr(investigate_mod, "post_json", fake_post)
    monkeypatch.setattr(investigate_mod, "latest_turn_number", fake_latest)
    monkeypatch.setattr(investigate_mod, "wait_sdk_closeout", fake_wait)

    out = await investigate_mod.default_investigate_hop(
        bound={"kind": "friction", "assertion_id": 1},
        model="cursor/claude-opus-5-5",
        prompt="probe",
    )
    assert out["ok"] is True
    assert out["wait_thread_id"] == "200"
    assert wait_pin["thread"] == "200"
    assert wait_pin["after_turn"] == 1
    assert wait_pin["execution_id"] == "exec-1"


@pytest.mark.asyncio
async def test_default_investigate_hop_repins_when_hint_omits_after_turn(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tips = {"100": 0, "200": 3}
    wait_pin: dict[str, object] = {}

    class _Client:
        async def __aenter__(self) -> _Client:
            return self

        async def __aexit__(self, *args: object) -> bool:
            return False

    async def fake_latest(
        _bus: object, thread_id: str, _headers: dict[str, str]
    ) -> int:
        return tips[thread_id]

    async def fake_post(
        _client: object,
        path: str,
        body: dict[str, object] | None = None,
        headers: dict[str, str] | None = None,
    ) -> dict[str, object]:
        if path == "/threads":
            return {"id": "100"}
        if path == "/api/v1/team/dispatch":
            return {
                "execution_id": "exec-2",
                "poll_hint": {"arguments": {"thread": "200"}},
            }
        return {"error": f"unexpected path {path}"}

    async def fake_wait(
        _bus: object,
        thread_id: str,
        *,
        headers: dict[str, str],
        after_turn: int,
        execution_id: str,
    ) -> dict[str, object]:
        wait_pin["thread"] = thread_id
        wait_pin["after_turn"] = after_turn
        return {"ok": True, "splice": "surfaces: repin"}

    monkeypatch.setattr(
        investigate_mod, "agent_bus_headers", lambda: {"Authorization": "Bearer x"}
    )
    monkeypatch.setattr(investigate_mod, "make_async_client", lambda *a, **k: _Client())
    monkeypatch.setattr(investigate_mod, "post_json", fake_post)
    monkeypatch.setattr(investigate_mod, "latest_turn_number", fake_latest)
    monkeypatch.setattr(investigate_mod, "wait_sdk_closeout", fake_wait)

    out = await investigate_mod.default_investigate_hop(
        bound={"kind": "friction", "assertion_id": 2},
        model="cursor/claude-opus-5-5",
        prompt="probe",
    )
    assert out["ok"] is True
    assert wait_pin["thread"] == "200"
    assert wait_pin["after_turn"] == 3


def test_resolve_turn_text_inline_splice_not_stolen_by_artifact_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """15474 B4 — artifact_paths must not replace an inline INVESTIGATE_SPLICE."""
    share = tmp_path / "universal-llm-gateway" / "tmp" / "reviews" / "closeouts"
    share.mkdir(parents=True)
    (share / "other.md").write_text("no splice here\n", encoding="utf-8")
    monkeypatch.setattr(
        investigate_wait_mod,
        "workspaces_root",
        lambda: tmp_path / "universal-llm-gateway",
    )
    inline = f"{SPLICE_START}\nsurfaces: inline\n{SPLICE_END}"
    text = investigate_wait_mod.resolve_turn_text({"body": inline})
    assert extract_investigate_splice(text) == "surfaces: inline"
    envelope = json.dumps(
        {
            "summary": "inline closeout",
            "evidence_uris": {
                "artifact_paths": [
                    "workspaces://universal-llm-gateway/tmp/reviews/closeouts/other.md"
                ]
            },
        }
    )
    assert investigate_wait_mod.closeout_source_ref(envelope) is None


def test_resolve_turn_text_follows_workspaces_source_ref(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    share = tmp_path / "universal-llm-gateway" / "tmp" / "reviews" / "closeouts"
    share.mkdir(parents=True)
    body_path = share / "x.md"
    splice = f"{SPLICE_START}\nsurfaces: from-share\n{SPLICE_END}"
    body_path.write_text(f"intro\n{splice}\n", encoding="utf-8")
    monkeypatch.setattr(
        investigate_wait_mod,
        "workspaces_root",
        lambda: tmp_path / "universal-llm-gateway",
    )
    envelope = json.dumps(
        {
            "source_ref": "workspaces://universal-llm-gateway/tmp/reviews/closeouts/x.md",
            "summary": "sidecar",
        }
    )
    text = investigate_wait_mod.resolve_turn_text({"body": envelope})
    assert "surfaces: from-share" in text
    assert extract_investigate_splice(text) == "surfaces: from-share"


def test_wait_client_timeout_exceeds_wait_slice() -> None:
    # Wrong ordering / client vs wait= mismatch: httpx 30s vs wait=55 fail-closes.
    assert WAIT_CLIENT_TIMEOUT > 55.0


class _TransportThenCompleteBus(_WaitBus):
    def __init__(self, *args: object, **kwargs: object) -> None:
        super().__init__(*args, **kwargs)
        self._raises_left = 1

    async def get(
        self,
        path: str,
        params: dict[str, object] | None = None,
        headers: dict[str, str] | None = None,
    ) -> _Resp:
        if path.endswith("/wait") and self._raises_left:
            self._raises_left -= 1
            self.paths.append(path)
            raise TimeoutError("")
        return await super().get(path, params, headers)


class _AlwaysTransportBus:
    def __init__(self) -> None:
        self.paths: list[str] = []

    async def get(
        self,
        path: str,
        params: dict[str, object] | None = None,
        headers: dict[str, str] | None = None,
    ) -> _Resp:
        self.paths.append(path)
        raise TimeoutError("")


class _ConnectThenCompleteBus(_WaitBus):
    def __init__(self, *args: object, raises: int = 5, **kwargs: object) -> None:
        super().__init__(*args, **kwargs)
        self._raises_left = raises

    async def get(
        self,
        path: str,
        params: dict[str, object] | None = None,
        headers: dict[str, str] | None = None,
    ) -> _Resp:
        if path.endswith("/wait") and self._raises_left:
            self._raises_left -= 1
            self.paths.append(path)
            raise httpx.ConnectError("connection refused")
        return await super().get(path, params, headers)


@pytest.fixture
def instant_wait_backoff(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    sleeps: list[float] = []

    async def _sleep(seconds: float) -> None:
        sleeps.append(seconds)

    monkeypatch.setattr(investigate_wait_mod.asyncio, "sleep", _sleep)
    return sleeps


@pytest.mark.asyncio
async def test_wait_sdk_closeout_retries_transport_after_admit(
    instant_wait_backoff: list[float],
) -> None:
    splice = f"{SPLICE_START}\nsurfaces: live\n{SPLICE_END}"
    bus = _TransportThenCompleteBus(
        wait_snapshot_fixture(
            qualifying_reply_turn=2, complete=True, status="complete"
        ),
        {"turn_number": 2, "from": "cursor-sdk", "body": splice},
    )
    out = await wait_sdk_closeout(
        bus,
        "99",
        headers={"Authorization": "Bearer x"},
        after_turn=0,
        execution_id="exec-admitted",
    )
    assert out["ok"] is True
    assert out["splice"] == "surfaces: live"
    assert bus.paths.count("/threads/99/wait") == 2


@pytest.mark.asyncio
async def test_wait_sdk_closeout_transport_fail_closed_before_admit() -> None:
    bus = _AlwaysTransportBus()
    out = await wait_sdk_closeout(
        bus,
        "99",
        headers={"Authorization": "Bearer x"},
        after_turn=0,
        execution_id="",
    )
    assert out["ok"] is False
    assert out["failure_class"] == "wait_transport"
    assert bus.paths == ["/threads/99/wait"]


@pytest.mark.asyncio
async def test_wait_sdk_closeout_transport_exhausted_after_admit(
    instant_wait_backoff: list[float],
) -> None:
    bus = _AlwaysTransportBus()
    out = await wait_sdk_closeout(
        bus,
        "99",
        headers={"Authorization": "Bearer x"},
        after_turn=0,
        execution_id="exec-admitted",
    )
    assert out["ok"] is False
    assert out["failure_class"] == "wait_transport"
    assert len(bus.paths) == 24
    assert instant_wait_backoff == [wait_transport_backoff_s(i) for i in range(24)]


@pytest.mark.asyncio
async def test_wait_sdk_closeout_connect_error_backoff_then_complete(
    instant_wait_backoff: list[float],
) -> None:
    splice = f"{SPLICE_START}\nsurfaces: live\n{SPLICE_END}"
    bus = _ConnectThenCompleteBus(
        wait_snapshot_fixture(
            qualifying_reply_turn=2, complete=True, status="complete"
        ),
        {"turn_number": 2, "from": "cursor-sdk", "body": splice},
        raises=5,
    )
    out = await wait_sdk_closeout(
        bus,
        "99",
        headers={"Authorization": "Bearer x"},
        after_turn=0,
        execution_id="exec-admitted",
    )
    assert out["ok"] is True
    assert instant_wait_backoff == [wait_transport_backoff_s(i) for i in range(5)]
    assert bus.paths.count("/threads/99/wait") == 6
