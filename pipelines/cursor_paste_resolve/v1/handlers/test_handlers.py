"""Unit tests for cursor-paste-resolve compose + launch handlers."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml
from systems.pipeline.core.pipeline_config import PipelineSpec
from work_key_grammar import is_valid_work_key_scheme

from ._message import (
    compose_message,
    cursor_sdk_dispatch_body,
    parse_compose_options,
    team_dispatch_admit_shape,
    work_key_for,
)
from .launch import CursorPasteLaunchHandler, bridge_argv, paste_thread_name

pytestmark = pytest.mark.offline

_YAML = Path(__file__).resolve().parent.parent / "cursor-paste-resolve-v1.yaml"


def test_pipeline_yaml_loads() -> None:
    data = yaml.safe_load(_YAML.read_text(encoding="utf-8"))
    spec = PipelineSpec(**data)
    assert spec.id == "cursor-paste-resolve"
    assert spec.steps[0].type == "cursor_paste_resolve_compose_v1"
    assert spec.steps[1].type == "cursor_paste_resolve_launch_v1"
    opts = spec.options.to_context_dict()
    assert "launch_target" in opts


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
async def test_unknown_launch_target_is_422() -> None:
    handler = CursorPasteLaunchHandler()
    ctx = SimpleNamespace(
        options={
            "kind": "friction",
            "assertion_id": 1,
            "launch_target": "wayland",
        },
        outputs={},
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
            "compose": SimpleNamespace(json={"message_path": str(msg)}),
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
        outputs={"compose": SimpleNamespace(json={"message_path": str(msg)})},
    )
    out = await handler.execute(SimpleNamespace(handler_inputs={}), ctx)
    assert out.json["ok"] is True
    argv = captured["argv"]
    assert "open-tab" in argv
    assert str(msg) in argv
    env = captured["env"]
    assert env["CURSOR_BRIDGE_SSH_HOST"] == "orion-node"
    assert env["CURSOR_BRIDGE_WINDOW"] == "glass"


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
        outputs={"compose": SimpleNamespace(json={"message_path": str(msg)})},
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
            "compose": SimpleNamespace(json={"message_path": "/no/such/file.md"}),
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
    )
    assert shape["model"] == "cursor/grok-4.7"
    assert shape["work_key"] == "adhoc:cursor-paste-assertion-7"
    assert is_valid_work_key_scheme(shape["work_key"])
    assert shape["dispatch_thread_id"] == "999"
    friction = work_key_for("friction", 7)
    assert friction == "friction:7"
    assert is_valid_work_key_scheme(friction)
    body = cursor_sdk_dispatch_body(
        kind="friction",
        assertion_id=7,
        prompt="prompt",
        dispatch_thread_id="999",
    )
    assert body["model"] == "cursor/grok-4.7"
    assert body["job"] == "freeform"
    assert body["work_key"] == "friction:7"


def test_paste_thread_prefix() -> None:
    from datetime import UTC, datetime

    name = paste_thread_name(datetime(2026, 10, 3, 8, 9, 10, tzinfo=UTC))
    assert name == "paste-080910"
