"""SF1: nested implement commit witness."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from services.git_integration_worker.cursor_sdk_nested_witness import (
    _DISPATCH_ON_THREAD_RE,
    _nested_child_has_commits,
    nested_implement_has_commits,
    nested_parent_with_commits,
    witness_ledger_path,
)

pytestmark = pytest.mark.offline

_RETRY_DISPATCH_ID = "bd8e467f-0c5c-4631-80f8-a13f6548eaba-r1"


def test_dispatch_on_thread_re_captures_retry_suffix() -> None:
    tip = f"hop 1 dispatch `{_RETRY_DISPATCH_ID}` on thread 14076."
    match = _DISPATCH_ON_THREAD_RE.search(tip)
    assert match is not None
    assert match.group(1) == _RETRY_DISPATCH_ID
    assert match.group(2) == "14076"


def test_nested_child_has_commits_from_closeout_body() -> None:
    assert (
        _nested_child_has_commits(
            dispatch_id="child-1",
            contract="implement",
            status="completed",
            record_json=json.dumps(
                {
                    "closeout_body": (
                        '{"status":"complete","commits_ahead":2,"head_sha":"abc"}'
                    )
                }
            ),
            wt_baseline=json.dumps({"admit_head": "deadbeef"}),
            source_repo=None,
            worktree_path=None,
        )
        is True
    )


def test_nested_child_has_commits_false_for_zero_commits() -> None:
    assert (
        _nested_child_has_commits(
            dispatch_id="child-2",
            contract="implement",
            status="completed",
            record_json=json.dumps(
                {"closeout_body": '{"status":"complete","commits_ahead":0}'}
            ),
            wt_baseline=json.dumps({"admit_head": "deadbeef"}),
            source_repo=None,
            worktree_path=None,
        )
        is False
    )


def test_nested_child_has_commits_true_for_contract_none() -> None:
    assert (
        _nested_child_has_commits(
            dispatch_id="child-none",
            contract="none",
            status="completed",
            record_json=json.dumps(
                {"closeout_body": '{"status":"complete","commits_ahead":2}'}
            ),
            wt_baseline=json.dumps({"admit_head": "deadbeef"}),
            source_repo=None,
            worktree_path=None,
        )
        is True
    )


def test_witness_ledger_path_prefers_production_when_data_dir_differs(
    tmp_path, monkeypatch
) -> None:
    home = tmp_path / "operator"
    gateway = home / ".gateway"
    gateway.mkdir(parents=True)
    home_db = gateway / "cursor-sdk-dispatch.db"
    home_db.write_bytes(b"")
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    monkeypatch.setenv("DATA_DIR", str(scratch))
    monkeypatch.delenv("CURSOR_SDK_DISPATCH_LEDGER", raising=False)
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_home.operator_real_home",
        lambda: home,
    )
    assert witness_ledger_path() == home_db


def test_witness_ledger_path_none_when_data_dir_unset(tmp_path, monkeypatch) -> None:
    home = tmp_path / "home"
    gateway = home / ".gateway"
    gateway.mkdir(parents=True)
    (gateway / "cursor-sdk-dispatch.db").write_bytes(b"")
    monkeypatch.delenv("DATA_DIR", raising=False)
    monkeypatch.setattr(Path, "home", lambda: home)
    assert witness_ledger_path() is None


def test_nested_implement_has_commits_reads_ledger_children(monkeypatch) -> None:
    monkeypatch.delenv("DATA_DIR", raising=False)
    row = {
        "dispatch_id": "child-nested-sf1",
        "contract": "implement",
        "status": "completed",
        "record_json": json.dumps(
            {
                "closeout_body": '{"status":"complete","commits_ahead":1}',
            }
        ),
        "wt_baseline": json.dumps({"admit_head": "deadbeef"}),
        "source_repo": None,
    }
    conn = MagicMock()
    conn.execute.return_value.fetchone.return_value = row
    conn.__enter__.return_value = conn
    conn.__exit__.return_value = None
    ledger = MagicMock()
    ledger.list_nested_children.return_value = ["child-nested-sf1"]
    ledger._connect.return_value = conn
    with patch(
        "services.git_integration_worker.cursor_dispatch_ledger.CursorDispatchLedger"
    ) as ledger_cls:
        ledger_cls.instance.return_value = ledger
        assert (
            nested_implement_has_commits(nest_under_dispatch_id="parent-conductor-sf1")
            is True
        )
    sql = conn.execute.call_args[0][0]
    assert "worktree_path" not in sql


def test_nested_implement_has_commits_true_for_mechanical(monkeypatch) -> None:
    """A terminal nested mechanical child with commits_ahead>0 witnesses G5."""
    monkeypatch.delenv("DATA_DIR", raising=False)
    row = {
        "dispatch_id": "child-mechanical-g5",
        "contract": "mechanical",
        "status": "completed",
        "record_json": json.dumps(
            {
                "closeout_body": '{"status":"complete","commits_ahead":1}',
            }
        ),
        "wt_baseline": json.dumps({"admit_head": "deadbeef"}),
        "source_repo": None,
    }
    conn = MagicMock()
    conn.execute.return_value.fetchone.return_value = row
    conn.__enter__.return_value = conn
    conn.__exit__.return_value = None
    ledger = MagicMock()
    ledger.list_nested_children.return_value = ["child-mechanical-g5"]
    ledger._connect.return_value = conn
    with patch(
        "services.git_integration_worker.cursor_dispatch_ledger.CursorDispatchLedger"
    ) as ledger_cls:
        ledger_cls.instance.return_value = ledger
        assert (
            nested_implement_has_commits(nest_under_dispatch_id="parent-conductor-g5")
            is True
        )


def _init_ledger(path: Path, rows: list[tuple], *, with_resume_of: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    cols = (
        "dispatch_id TEXT PRIMARY KEY, contract TEXT, status TEXT, record_json TEXT, "
        "wt_baseline TEXT, source_repo TEXT, thread_id TEXT"
    )
    if with_resume_of:
        cols += ", resume_of TEXT"
    conn.execute(f"CREATE TABLE cursor_sdk_dispatches ({cols})")
    if rows:
        placeholders = ", ".join("?" for _ in rows[0])
        conn.executemany(
            f"INSERT INTO cursor_sdk_dispatches VALUES ({placeholders})",
            rows,
        )
    conn.commit()
    conn.close()


def _commits_record(nest_under: str) -> str:
    return json.dumps(
        {
            "nest_under": nest_under,
            "closeout_body": '{"status":"complete","commits_ahead":1}',
        }
    )


@pytest.mark.parametrize("contract", ["pure-mechanical", "mechanical"])
def test_fold_counts_pure_mechanical_nested_child(
    tmp_path, monkeypatch, contract: str
) -> None:
    home = tmp_path / "operator"
    prod_db = home / ".gateway" / "cursor-sdk-dispatch.db"
    _init_ledger(
        prod_db,
        [
            (
                "child-fold",
                contract,
                "completed",
                _commits_record("parent-fold"),
                json.dumps({"admit_head": "deadbeef"}),
                None,
                "13998",
            )
        ],
    )
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "scratch"))
    monkeypatch.delenv("CURSOR_SDK_DISPATCH_LEDGER", raising=False)
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_home.operator_real_home",
        lambda: home,
    )
    assert nested_implement_has_commits(nest_under_dispatch_id="parent-fold") is True


@pytest.mark.parametrize("contract", ["none", "freeform"])
def test_fold_counts_freeform_none_nested_child(
    tmp_path, monkeypatch, contract: str
) -> None:
    home = tmp_path / "operator"
    prod_db = home / ".gateway" / "cursor-sdk-dispatch.db"
    _init_ledger(
        prod_db,
        [
            (
                "child-fold",
                contract,
                "completed",
                _commits_record("parent-fold"),
                json.dumps({"admit_head": "deadbeef"}),
                None,
                "13998",
            )
        ],
    )
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "scratch"))
    monkeypatch.delenv("CURSOR_SDK_DISPATCH_LEDGER", raising=False)
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_home.operator_real_home",
        lambda: home,
    )
    assert nested_implement_has_commits(nest_under_dispatch_id="parent-fold") is True


def test_fold_counts_nested_grandchild_commits(tmp_path, monkeypatch) -> None:
    home = tmp_path / "operator"
    prod_db = home / ".gateway" / "cursor-sdk-dispatch.db"
    _init_ledger(
        prod_db,
        [
            (
                "mid-fold",
                "implement",
                "completed",
                json.dumps({"nest_under": "parent-fold"}),
                None,
                None,
                "13998",
            ),
            (
                "grand-fold",
                "implement",
                "completed",
                _commits_record("mid-fold"),
                json.dumps({"admit_head": "deadbeef"}),
                None,
                "13998",
            ),
        ],
    )
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "scratch"))
    monkeypatch.delenv("CURSOR_SDK_DISPATCH_LEDGER", raising=False)
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_home.operator_real_home",
        lambda: home,
    )
    assert nested_implement_has_commits(nest_under_dispatch_id="parent-fold") is True


def test_fold_counts_resume_of_child_commits(tmp_path, monkeypatch) -> None:
    home = tmp_path / "operator"
    prod_db = home / ".gateway" / "cursor-sdk-dispatch.db"
    _init_ledger(
        prod_db,
        [
            (
                "resume-child",
                "none",
                "completed",
                json.dumps(
                    {
                        "closeout_body": '{"status":"complete","commits_ahead":2}',
                    }
                ),
                json.dumps({"admit_head": "deadbeef"}),
                None,
                "13998",
                "parent-fold",
            )
        ],
        with_resume_of=True,
    )
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "scratch"))
    monkeypatch.delenv("CURSOR_SDK_DISPATCH_LEDGER", raising=False)
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_home.operator_real_home",
        lambda: home,
    )
    assert nested_implement_has_commits(nest_under_dispatch_id="parent-fold") is True


def test_nested_implement_reads_production_ledger_when_data_dir_differs(
    tmp_path, monkeypatch
) -> None:
    """Folding process DATA_DIR is not the ledger that holds the nested child."""
    home = tmp_path / "operator"
    prod_db = home / ".gateway" / "cursor-sdk-dispatch.db"
    child_record = json.dumps(
        {
            "nest_under": "parent-prod",
            "closeout_body": '{"status":"complete","commits_ahead":1}',
        }
    )
    _init_ledger(
        prod_db,
        [
            (
                "child-prod",
                "none",
                "completed",
                child_record,
                json.dumps({"admit_head": "deadbeef"}),
                None,
                "13263",
            )
        ],
    )
    scratch = tmp_path / "scratch"
    _init_ledger(scratch / "cursor-sdk-dispatch.db", [])
    monkeypatch.setenv("DATA_DIR", str(scratch))
    monkeypatch.delenv("CURSOR_SDK_DISPATCH_LEDGER", raising=False)
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_home.operator_real_home",
        lambda: home,
    )
    assert nested_implement_has_commits(nest_under_dispatch_id="parent-prod") is True


def test_parent_with_commits_finds_thread_mate_not_named_on_the_tip(
    tmp_path, monkeypatch
) -> None:
    home = tmp_path / "operator"
    prod_db = home / ".gateway" / "cursor-sdk-dispatch.db"
    child_record = json.dumps(
        {
            "nest_under": "auto-d472d61ad300",
            "closeout_body": '{"commits_ahead":2}',
        }
    )
    _init_ledger(
        prod_db,
        [
            (
                "cdcf4de7419b-8b9e9196",
                "conductor",
                "completed",
                "{}",
                None,
                None,
                "13263",
            ),
            (
                "auto-d472d61ad300",
                "none",
                "completed",
                "{}",
                None,
                None,
                "13263",
            ),
            (
                "34fba76c33b1-3df12bdb",
                "none",
                "completed",
                child_record,
                None,
                None,
                "13301",
            ),
        ],
    )
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    monkeypatch.setenv("DATA_DIR", str(scratch))
    monkeypatch.delenv("CURSOR_SDK_DISPATCH_LEDGER", raising=False)
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_home.operator_real_home",
        lambda: home,
    )
    tip = "hop 1 dispatch `cdcf4de7419b-8b9e9196` on thread 13263. G3 plan nest."
    assert (
        nested_parent_with_commits(tip_body=tip, explicit_parent_id=None)
        == "auto-d472d61ad300"
    )
