"""Contract tests for the direct fleet_liveness MCP registration."""

from __future__ import annotations

import inspect

from tools import fleet_liveness


class _Recorder:
    """Minimal FastMCP decorator recorder for registration tests."""

    def __init__(self) -> None:
        self.functions = {}

    def tool(self, **_kwargs):
        def decorate(fn):
            self.functions[fn.__name__] = fn
            return fn

        return decorate


def test_registers_zero_argument_read_only_wrapper() -> None:
    recorder = _Recorder()
    fleet_liveness.register_fleet_liveness_tools(recorder)  # type: ignore[arg-type]
    fn = recorder.functions["fleet_liveness"]
    assert list(inspect.signature(fn).parameters) == []


def test_wrapper_forwards_manage_snapshot(monkeypatch) -> None:
    recorder = _Recorder()
    fleet_liveness.register_fleet_liveness_tools(recorder)  # type: ignore[arg-type]
    monkeypatch.setattr(
        fleet_liveness,
        "_call_manage",
        lambda body, timeout: {"result": {"schema_version": 1}},
    )
    monkeypatch.setattr(
        fleet_liveness,
        "_extract_result",
        lambda raw: raw["result"],
    )
    assert recorder.functions["fleet_liveness"]() == {"schema_version": 1}
