"""list_nested_children indexed path + terminal skip (cpu-pin B)."""

from __future__ import annotations

import json

import pytest

from services.git_integration_worker.cursor_dispatch_ledger import (
    CursorDispatchLedger,
    clear_nested_children_cache,
    _connect,
)
from services.git_integration_worker.models.cursor_api import CursorDispatchRequest


@pytest.fixture(autouse=True)
def _iso(tmp_path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    CursorDispatchLedger._instance = None
    clear_nested_children_cache()
    yield
    CursorDispatchLedger._instance = None
    clear_nested_children_cache()


def _insert(
    ledger: CursorDispatchLedger,
    *,
    dispatch_id: str,
    nest_under: str | None = None,
    status: str = "running",
) -> None:
    req = CursorDispatchRequest(
        thread_id="t1",
        model="cursor/composer-2.5",
        dispatch_id=dispatch_id,
        execution_id=f"exec-{dispatch_id}",
        message=f"msg-{dispatch_id}",
    )
    wf = ledger.work_fingerprint(req)
    with _connect() as conn:
        conn.execute(
            "INSERT INTO cursor_sdk_dispatches "
            "(dispatch_id, fingerprint, thread_id, execution_id, resolved_model, "
            " message_present, status, record_json, source_repo, lease_key, "
            " read_only, work_fingerprint, nest_under) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                dispatch_id,
                ledger.fingerprint(req),
                "t1",
                req.execution_id,
                "composer-2.5",
                1,
                status,
                json.dumps(
                    {
                        "model": req.model,
                        "message": req.message,
                        "nest_under": nest_under,
                    }
                ),
                "/repo",
                "/repo",
                0,
                wf,
                nest_under,
            ),
        )


def test_list_nested_children_uses_nest_under_column() -> None:
    ledger = CursorDispatchLedger.instance()
    _insert(ledger, dispatch_id="parent", status="running")
    _insert(ledger, dispatch_id="child", nest_under="parent")
    clear_nested_children_cache()
    assert ledger.list_nested_children(parent_dispatch_id="parent") == ["child"]


def test_list_nested_children_returns_child_for_completed_parent() -> None:
    ledger = CursorDispatchLedger.instance()
    _insert(ledger, dispatch_id="parent", status="completed")
    _insert(ledger, dispatch_id="child", nest_under="parent")
    clear_nested_children_cache()
    assert ledger.list_nested_children(parent_dispatch_id="parent") == ["child"]
