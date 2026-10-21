"""Hermetic ingest_dropbox MCP schema and unknown-op rejection."""

from __future__ import annotations

import inspect

import pytest

from tools.ingest_dropbox import register_ingest_dropbox_tools


class _Recorder:
    def __init__(self) -> None:
        self.functions: dict = {}

    def tool(self, **_kwargs):
        def decorator(fn):
            self.functions[fn.__name__] = fn
            return fn

        return decorator


def _ingest_fn():
    recorder = _Recorder()
    register_ingest_dropbox_tools(recorder)  # type: ignore[arg-type]
    return recorder.functions["ingest_dropbox"]


def test_served_op_enum_is_run_dry_run_status_only() -> None:
    fn = _ingest_fn()
    op_ann = str(inspect.signature(fn).parameters["op"].annotation)
    for name in ("run", "dry_run", "status"):
        assert name in op_ann
    assert "list" not in op_ann


def test_unknown_op_list_raises_value_error() -> None:
    fn = _ingest_fn()
    with pytest.raises(ValueError, match="Unknown ingest_dropbox op 'list'"):
        fn(op="list")  # type: ignore[arg-type]
