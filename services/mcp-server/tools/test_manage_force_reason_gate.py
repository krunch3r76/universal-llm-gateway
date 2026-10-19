"""MCP manage forwards force_reason and exposes it on the tool signature."""

from __future__ import annotations

import inspect

import pytest

from tools import manage as manage_mod

pytestmark = pytest.mark.offline


class _Recorder:
    def __init__(self) -> None:
        self.functions: dict = {}

    def tool(self, **_kwargs):
        def decorate(fn):
            self.functions[fn.__name__] = fn
            return fn

        return decorate


def _manage_fn():
    recorder = _Recorder()
    manage_mod.register_manage_tools(recorder)  # type: ignore[arg-type]
    return recorder.functions["manage"]


def test_manage_tool_exposes_force_reason_parameter() -> None:
    params = inspect.signature(_manage_fn()).parameters
    assert "force_reason" in params
    assert "caller_thread_id" in params


def test_manage_forwards_force_reason_and_thread(monkeypatch) -> None:
    manage = _manage_fn()
    captured: list[dict] = []

    def _capture(body, timeout):
        captured.append(body)
        return {"result": {"status": "ok"}}

    monkeypatch.setattr(manage_mod, "_call_manage", _capture)
    monkeypatch.setattr(manage_mod, "_extract_result", lambda raw: raw["result"])
    monkeypatch.setenv("ULG_DISPATCH_THREAD_ID", "18180")
    manage(
        action="sync_restart",
        service="mcp",
        force=True,
        force_reason="coordinator judgment",
    )
    params = captured[0]["params"]
    assert params.get("force") is True
    assert params["force_reason"] == "coordinator judgment"
    assert params["caller_thread_id"] == "18180"
    assert params["self_call_counted"] is True

    manage(
        action="sync_restart",
        service="gateway",
        force=True,
        force_reason="coordinator judgment",
    )
    assert "self_call_counted" not in captured[1]["params"]


def test_giw_force_still_refused_at_mcp_layer(monkeypatch) -> None:
    manage = _manage_fn()
    monkeypatch.setattr(
        manage_mod,
        "_call_manage",
        lambda *_a, **_k: (_ for _ in ()).throw(
            AssertionError("_call_manage must not run")
        ),
    )
    result = manage(
        action="sync_restart",
        service="git_integration_worker",
        force=True,
        force_reason="should not matter",
    )
    assert result["reason"] == "giw_force_refused"
