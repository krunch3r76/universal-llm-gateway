"""Contract tests for the life-surface recycle_giw MCP registration."""

from __future__ import annotations

import inspect
from pathlib import Path

from tools import recycle_giw


class _Recorder:
    """Minimal FastMCP decorator recorder for registration tests."""

    def __init__(self) -> None:
        self.functions = {}

    def tool(self, **_kwargs):
        def decorate(fn):
            self.functions[fn.__name__] = fn
            return fn

        return decorate


def test_registers_zero_argument_wrapper() -> None:
    recorder = _Recorder()
    recycle_giw.register_recycle_giw_tools(recorder)  # type: ignore[arg-type]
    fn = recorder.functions["recycle_giw"]
    assert list(inspect.signature(fn).parameters) == []


def test_wrapper_hardcodes_recycle_giw_method(monkeypatch) -> None:
    recorder = _Recorder()
    recycle_giw.register_recycle_giw_tools(recorder)  # type: ignore[arg-type]
    captured: dict = {}

    def _fake_call(body, timeout):
        captured["body"] = body
        captured["timeout"] = timeout
        return {"result": {"status": "deferred", "service": "git_integration_worker"}}

    monkeypatch.setattr(recycle_giw, "_call_manage", _fake_call)
    monkeypatch.setattr(recycle_giw, "_extract_result", lambda raw: raw["result"])
    result = recorder.functions["recycle_giw"]()
    assert captured["body"]["method"] == "recycle_giw"
    assert captured["body"]["params"] == {}
    assert "service" not in captured["body"]["params"]
    assert "action" not in captured["body"]["params"]
    assert result["status"] == "deferred"


_QUEUE_TOKENS = (
    "claim_next",
    "claim_next_concurrent",
    "claim_job",
    "get_queue()",
    "AutoJobQueue",
    "cursor_auto.queue",
    "agent_bus.request",
)


def test_wrapper_source_has_no_auto_queue_admit() -> None:
    """Wedged-queue case: the life trigger must not mention serial admit APIs."""
    source = Path(recycle_giw.__file__).read_text()
    for token in _QUEUE_TOKENS:
        assert token not in source, token
    assert "_call_manage" in source
    assert "AF_UNIX" not in source  # UDS lives in manage._call_manage, not here


def test_call_manage_is_unix_socket_not_giw_http() -> None:
    """Trigger transport is manage.sock UDS, not GIW HTTP or AutoJobQueue."""
    manage_src = (Path(recycle_giw.__file__).parent / "manage.py").read_text()
    assert "socket.AF_UNIX" in manage_src
    assert "MANAGE_SOCKET" in manage_src
    assert "claim_next" not in manage_src
    assert "AutoJobQueue" not in manage_src


def test_wedged_queue_claim_next_is_never_invoked(monkeypatch) -> None:
    """Fire the sliver while claim_next would explode — the wrapper must not call it."""
    recorder = _Recorder()
    recycle_giw.register_recycle_giw_tools(recorder)  # type: ignore[arg-type]
    calls: list[str] = []

    def _boom(*_args, **_kwargs):
        calls.append("claim_next")
        raise AssertionError("AutoJobQueue.claim_next must not run on recycle_giw")

    monkeypatch.setattr(
        "services.git_integration_worker.cursor_auto.queue.AutoJobQueue.claim_next",
        _boom,
        raising=False,
    )
    monkeypatch.setattr(
        recycle_giw,
        "_call_manage",
        lambda body, timeout: {"result": {"status": "deferred", "method": body["method"]}},
    )
    monkeypatch.setattr(recycle_giw, "_extract_result", lambda raw: raw["result"])
    result = recorder.functions["recycle_giw"]()
    assert result["status"] == "deferred"
    assert calls == []
