"""MCP registration test for panel_dispatch."""

from __future__ import annotations

import inspect
from typing import Any

from tools.panel_dispatch import register_panel_dispatch_tools


class _ToolNameRecorder:
    def __init__(self) -> None:
        self.registered: list[str] = []
        self.functions: dict[str, Any] = {}

    def tool(self, **_kwargs: Any) -> Any:
        def decorator(fn: Any) -> Any:
            self.registered.append(fn.__name__)
            self.functions[fn.__name__] = fn
            return fn

        return decorator


def test_panel_dispatch_registered() -> None:
    recorder = _ToolNameRecorder()
    register_panel_dispatch_tools(recorder)  # type: ignore[arg-type]
    assert "panel_dispatch" in recorder.registered


def test_panel_dispatch_requires_dispatch_thread_id() -> None:
    recorder = _ToolNameRecorder()
    register_panel_dispatch_tools(recorder)
    sig = inspect.signature(recorder.functions["panel_dispatch"])
    assert "dispatch_thread_id" in sig.parameters
    assert sig.parameters["dispatch_thread_id"].default is inspect.Parameter.empty
