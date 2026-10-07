"""Jobs dispatch ops are stamped. Removing one stamp makes it unbound."""

from __future__ import annotations

import copy

import pytest

from jobs.openapi_mcp._route_map import unbound_dispatch_ops
from jobs.server import create_app


@pytest.mark.offline
def test_unbound_dispatch_ops_empty(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JOBS_STATE_DIR", str(tmp_path))
    monkeypatch.setenv("JOBS_TOKEN", "test-token")
    assert unbound_dispatch_ops(create_app().openapi()) == []


@pytest.mark.offline
def test_removed_create_run_stamp_is_unbound(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JOBS_STATE_DIR", str(tmp_path))
    monkeypatch.setenv("JOBS_TOKEN", "test-token")
    schema = copy.deepcopy(create_app().openapi())
    for methods in schema["paths"].values():
        for spec in methods.values():
            if isinstance(spec, dict) and spec.get("x-mcp", {}).get("op") == "create_run":
                del spec["x-mcp"]
    assert "create_run" in unbound_dispatch_ops(schema)
