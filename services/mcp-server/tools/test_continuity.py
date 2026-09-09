"""Hermetic tests for continuity MCP tool — mocked Stargate + bus reads."""

from __future__ import annotations

import inspect
from typing import Any
from unittest.mock import patch

import pytest

from agent_bus_store.continuity_watermark import SEEDED_BY
from tools import continuity
from tools.continuity import _continuity_status_watermark

pytestmark = pytest.mark.offline


class _Recorder:
    def __init__(self) -> None:
        self.functions: dict[str, Any] = {}

    def tool(self, **_kwargs):
        def decorate(fn):
            self.functions[fn.__name__] = fn
            return fn

        return decorate


@pytest.fixture()
def continuity_fn():
    recorder = _Recorder()
    continuity.register_continuity_tools(recorder)  # type: ignore[arg-type]
    return recorder.functions["continuity"]


def test_registers_continuity_tool() -> None:
    recorder = _Recorder()
    continuity.register_continuity_tools(recorder)  # type: ignore[arg-type]
    assert "continuity" in recorder.functions
    params = list(inspect.signature(recorder.functions["continuity"]).parameters)
    assert params[0] == "op"
    assert "trigger_thread" in params
    assert "turn" in params


def test_replay_dry_run_defaults_true_and_force_defaults_true(continuity_fn) -> None:
    captured: dict[str, Any] = {}

    def _fake_prepare(**kwargs):
        captured.update(kwargs)
        options = {
            "root_thread": "10223",
            "trigger": {"thread": "10303", "turn": 112},
        }
        if kwargs.get("dry_run"):
            options["dry_run"] = True
        if kwargs.get("force"):
            options["force"] = True
        return "10223", options

    def _fake_dispatch(options, *, dispatch_thread_id):
        captured["options"] = options
        captured["dispatch_thread_id"] = dispatch_thread_id
        return {"execution_id": "exec-replay-1"}

    with (
        patch.object(continuity, "_prepare_consolidate", side_effect=_fake_prepare),
        patch.object(continuity, "_continuity_async_dispatch", side_effect=_fake_dispatch),
    ):
        result = continuity_fn(op="replay", trigger_thread="10303", turn=112)

    assert result["execution_id"] == "exec-replay-1"
    assert captured["dry_run"] is True
    assert captured["force"] is True
    assert captured["options"]["dry_run"] is True
    assert captured["options"]["force"] is True
    assert captured["dispatch_thread_id"] == "10223"


def test_consolidate_dispatches_with_execution_id(continuity_fn) -> None:
    posted: dict[str, Any] = {}

    class _Resp:
        status_code = 200

        @staticmethod
        def json():
            return {"execution_id": "exec-consolidate-9"}

    class _Client:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def post(self, url, json=None):
            posted["url"] = url
            posted["body"] = json
            return _Resp()

    with (
        patch("tools.continuity.resolve_root", return_value="10223"),
        patch(
            "tools.continuity.build_consolidate_options",
            return_value={
                "root_thread": "10223",
                "root": {"slug": "house"},
                "trigger": {"thread": "10303", "turn": 54, "subject": "CLOSEOUT"},
            },
        ),
        patch("tools.continuity.make_sync_client", return_value=_Client()),
    ):
        result = continuity_fn(op="consolidate", trigger_thread="10303", turn=54)

    assert result["execution_id"] == "exec-consolidate-9"
    assert posted["url"] == "/api/v1/pipelines/dispatch"
    body = posted["body"]
    assert body["model"] == "consolidate-continuity"
    assert body["dispatch_thread_id"] == "10223"
    assert body["pipeline_options"]["root_thread"] == "10223"
    assert body["caller_agent"] == "agent-bus:continuity-consolidate"
    assert "dry_run" not in body["pipeline_options"]
    assert "force" not in body["pipeline_options"]


def test_invalid_root_returns_no_root_house_422(continuity_fn) -> None:
    with patch("tools.continuity.resolve_root", return_value=None):
        result = continuity_fn(op="consolidate", trigger_thread="99999", turn=1)

    assert result["status_code"] == 422
    assert result["error"]["code"] == "no_root_house"


def test_status_execution_id_delegates_to_pipeline_result(continuity_fn) -> None:
    tracker = {"execution_id": "exec-1", "status": "running"}
    with patch.object(continuity, "_pipeline_result", return_value=tracker) as mock_result:
        result = continuity_fn(op="status", execution_id="exec-1", wait_seconds=2.0)

    assert result == tracker
    mock_result.assert_called_once_with("exec-1", 2.0)


def test_status_root_thread_reads_watermark(continuity_fn) -> None:
    assertions_payload = {
        "assertions": [
            {
                "id": 99,
                "seeded_by": "continuity-consolidate",
                "claim": "WATERMARK: consolidated_through=10303#112",
            }
        ]
    }

    with patch.object(continuity, "cx", return_value=assertions_payload):
        result = continuity_fn(op="status", root_thread="10223")

    assert result["root_thread"] == "10223"
    assert result["hub_entity_id"] == "document:10223-continuity"
    assert result["watermark"]["thread"] == "10303"
    assert result["watermark"]["turn"] == 112


def test_prepare_consolidate_applies_overrides() -> None:
    with (
        patch("tools.continuity.resolve_root", return_value="10223"),
        patch(
            "tools.continuity.build_consolidate_options",
            return_value={
                "root_thread": "10223",
                "trigger": {"thread": "10303", "turn": 112},
            },
        ),
    ):
        prepared = continuity._prepare_consolidate(
            trigger_thread="10303",
            turn=112,
            dry_run=True,
            force=True,
            model_ref_overrides={"consolidator": "openai/gpt-5.4"},
        )

    assert not isinstance(prepared, dict)
    root, options = prepared
    assert root == "10223"
    assert options["dry_run"] is True
    assert options["force"] is True
    assert options["model_ref_overrides"] == {"consolidator": "openai/gpt-5.4"}


def test_async_dispatch_http_error_shape() -> None:
    class _Resp:
        status_code = 503
        text = "busy"

        @staticmethod
        def json():
            raise ValueError("not json")

    class _Client:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def post(self, url, json=None):
            return _Resp()

    with patch.object(continuity, "make_sync_client", return_value=_Client()):
        result = continuity._continuity_async_dispatch(
            {"root_thread": "10223", "trigger": {"thread": "10303", "turn": 1}},
            dispatch_thread_id="10223",
        )

    assert result["status_code"] == 503
    assert "error" in result


def test_status_watermark_uses_shared_parse_newest_id_wins() -> None:
    """Falsifier: naive first-match parse would return thread-a#1, not thread-b#99."""
    assertions = [
        {
            "id": 10,
            "seeded_by": "manual-entry",
            "claim": "WATERMARK: consolidated_through=thread-a#1",
        },
        {
            "id": 42,
            "seeded_by": SEEDED_BY,
            "claim": "WATERMARK: consolidated_through=thread-b#99",
        },
    ]
    with patch(
        "tools.continuity.cx",
        return_value={"assertions": assertions},
    ):
        result = _continuity_status_watermark("root-house")

    assert result["root_thread"] == "root-house"
    assert result["hub_entity_id"] == "document:root-house-continuity"
    watermark = result["watermark"]
    assert watermark is not None
    assert watermark["assertion_id"] == 42
    assert watermark["thread"] == "thread-b"
    assert watermark["turn"] == 99


def test_checkpoint_missing_required(continuity_fn) -> None:
    result = continuity_fn(op="checkpoint", thread="10223")
    assert result["error"]["code"] == "missing_required"


def test_checkpoint_relay(continuity_fn) -> None:
    with patch(
        "tools._continuity_relays.make_sync_client",
    ) as mock_client:
        class _Resp:
            status_code = 202

            @staticmethod
            def json():
                return {"execution_id": "exec-cp-1", "status": "running"}

        class _Client:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def post(self, url, json=None):
                assert url == "/api/v1/continuity/checkpoint"
                assert json["surface"] == "cursor"
                return _Resp()

        mock_client.return_value = _Client()
        result = continuity_fn(
            op="checkpoint",
            thread="10223",
            surface="cursor",
            from_agent="cursor",
        )
    assert result["execution_id"] == "exec-cp-1"


def test_status_watermark_ignores_unseeded_rows() -> None:
    assertions = [
        {
            "id": 99,
            "seeded_by": "other-pipeline",
            "claim": "WATERMARK: consolidated_through=spoof#1",
        },
    ]
    with patch(
        "tools.continuity.cx",
        return_value={"assertions": assertions},
    ):
        result = _continuity_status_watermark("root-x")

    assert result["watermark"] is None
