"""list_nested_children merge, pass index, and db scoping (cpu-pin B)."""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from services.git_integration_worker.cursor_dispatch_ledger import (
    CursorDispatchLedger,
    _connect,
    build_nest_children_index,
    clear_nested_children_cache,
    nest_children_watchdog_pass,
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
    with ledger._connect() as conn:
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


def test_watchdog_pass_one_full_scan_matches_uncached() -> None:
    ledger = CursorDispatchLedger.instance()
    _insert(ledger, dispatch_id="root", status="completed")
    _insert(ledger, dispatch_id="c1", nest_under="root")
    _insert(ledger, dispatch_id="c2", nest_under="root")
    clear_nested_children_cache()
    uncached = ledger.list_nested_children(parent_dispatch_id="root")
    with patch(
        "services.git_integration_worker.cursor_dispatch_ledger.build_nest_children_index",
        wraps=build_nest_children_index,
    ) as build_mock:
        with ledger._connect() as conn:
            with nest_children_watchdog_pass(conn):
                indexed = [
                    ledger.list_nested_children(parent_dispatch_id="root")
                    for _ in range(40)
                ]
        assert build_mock.call_count == 1
    assert indexed[0] == uncached
    with ledger._connect() as conn2:
        idx = build_nest_children_index(conn2)
    assert list(idx.by_parent.get("root", [])) == uncached


def test_list_nested_children_merges_column_and_json_children() -> None:
    ledger = CursorDispatchLedger.instance()
    _insert(ledger, dispatch_id="parent", status="completed")
    _insert(ledger, dispatch_id="col-child", nest_under="parent")
    with _connect() as conn:
        req = CursorDispatchRequest(
            thread_id="t1",
            model="cursor/composer-2.5",
            dispatch_id="json-child",
            execution_id="exec-json-child",
            message="json",
        )
        conn.execute(
            "INSERT INTO cursor_sdk_dispatches "
            "(dispatch_id, fingerprint, thread_id, execution_id, resolved_model, "
            " message_present, status, record_json, source_repo, lease_key, "
            " read_only, work_fingerprint, nest_under) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)",
            (
                "json-child",
                ledger.fingerprint(req),
                "t1",
                req.execution_id,
                "composer-2.5",
                1,
                "running",
                json.dumps({"model": req.model, "message": req.message, "nest_under": "parent"}),
                "/repo",
                "/repo",
                0,
                ledger.work_fingerprint(req),
            ),
        )
    clear_nested_children_cache()
    children = ledger.list_nested_children(parent_dispatch_id="parent")
    assert set(children) == {"col-child", "json-child"}


def test_nest_watchdog_index_scoped_per_db(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    path_a = tmp_path / "ledger-a"
    path_b = tmp_path / "ledger-b"
    monkeypatch.setenv("DATA_DIR", str(path_a))
    CursorDispatchLedger._instance = None
    ledger_a = CursorDispatchLedger.instance()
    _insert(ledger_a, dispatch_id="parent-a", status="completed")
    _insert(ledger_a, dispatch_id="child-a", nest_under="parent-a")

    monkeypatch.setenv("DATA_DIR", str(path_b))
    CursorDispatchLedger._instance = None
    ledger_b = CursorDispatchLedger.instance()
    _insert(ledger_b, dispatch_id="parent-b", status="completed")
    _insert(ledger_b, dispatch_id="child-b", nest_under="parent-b")

    with ledger_a._connect() as conn:
        with nest_children_watchdog_pass(conn):
            assert ledger_a.list_nested_children(parent_dispatch_id="parent-a") == [
                "child-a"
            ]
            assert ledger_b.list_nested_children(parent_dispatch_id="parent-b") == [
                "child-b"
            ]


def test_build_nest_index_skips_json_load_for_non_nest_rows(tmp_path) -> None:
    ledger = CursorDispatchLedger.instance()
    with _connect() as conn:
        conn.execute(
            "INSERT INTO cursor_sdk_dispatches "
            "(dispatch_id, fingerprint, thread_id, resolved_model, status, "
            " message_present, read_only, record_json) "
            "VALUES ('plain', 'fp', 't', 'composer-2.5', 'running', 0, 0, ?)",
            (json.dumps({"message": "no nest"}),),
        )
    loads = 0
    real_loads = json.loads

    def counting_loads(text: str, *args, **kwargs):
        nonlocal loads
        loads += 1
        return real_loads(text, *args, **kwargs)

    with ledger._connect() as conn:
        with patch("json.loads", side_effect=counting_loads):
            build_nest_children_index(conn)
    assert loads == 0


def test_list_nested_children_includes_oldest_of_many(tmp_path) -> None:
    ledger = CursorDispatchLedger.instance()
    _insert(ledger, dispatch_id="wide-parent", status="completed")
    for i in range(40):
        _insert(ledger, dispatch_id=f"child-{i}", nest_under="wide-parent")
    children = ledger.list_nested_children(parent_dispatch_id="wide-parent")
    assert len(children) == 40
    assert children[-1] == "child-0"
