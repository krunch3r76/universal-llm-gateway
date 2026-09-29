"""Refuse force=true on git_integration_worker lifecycle via the manage MCP tool."""

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


def test_manage_giw_restart_force_refused_before_manage_sock(monkeypatch) -> None:
    manage = _manage_fn()
    captured: list[dict] = []

    def _unexpected(body, timeout):
        captured.append(body)
        raise AssertionError("_call_manage must not run when force is refused")

    monkeypatch.setattr(manage_mod, "_call_manage", _unexpected)
    result = manage(
        action="restart",
        service="git_integration_worker",
        force=True,
    )
    assert result["reason"] == "giw_force_refused"
    assert "force" not in (captured[0]["params"] if captured else {})
    assert captured == []
    assert result == manage_mod._refuse_giw_force()


def test_manage_mcp_restart_force_still_forwards_force(monkeypatch) -> None:
    manage = _manage_fn()
    captured: list[dict] = []

    def _capture(body, timeout):
        captured.append(body)
        return {"result": {"status": "ok"}}

    monkeypatch.setattr(manage_mod, "_call_manage", _capture)
    monkeypatch.setattr(manage_mod, "_extract_result", lambda raw: raw["result"])
    manage(action="restart", service="mcp", force=True)
    assert captured[0]["params"].get("force") is True


def test_refusal_applies_to_stop_and_sync_restart(monkeypatch) -> None:
    manage = _manage_fn()
    monkeypatch.setattr(
        manage_mod,
        "_call_manage",
        lambda *_a, **_k: (_ for _ in ()).throw(
            AssertionError("_call_manage must not run")
        ),
    )
    for action in ("stop", "sync_restart"):
        result = manage(
            action=action,
            service="git_integration_worker",
            force=True,
        )
        assert result["reason"] == "giw_force_refused"


def test_manage_tool_exposes_force_parameter() -> None:
    assert "force" in inspect.signature(_manage_fn()).parameters
