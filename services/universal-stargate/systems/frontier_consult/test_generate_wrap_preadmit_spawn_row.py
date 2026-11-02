"""friction:41315 A — the spawn row survives a materialize that dies.

The materialize at ``dispatch_cursor_sdk_generate_route`` is a held executor
call upstream of the GIW admit, so a request that raises or times out inside it
never reaches ``ledger.admit``. These tests pin the pre-materialize row and its
terminal patch.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from cursor_bridge.spawn_endpoint_house_ledger import (
    _fold_spawn_rows,
    _read_rows,
    ledger_path,
    mint_row_id,
)

from .generate_wrap import _fail_pending_spawn_row, _record_pending_spawn_row


class _Body:
    def __init__(self, **kw: Any) -> None:
        self.dispatch_thread_id = kw.get("dispatch_thread_id", "21723")
        self.caller_agent = kw.get("caller_agent", "cursor-sdk")
        self.dispatch_id = kw.get("dispatch_id")
        self.model = kw.get("model", "cursor/composer-2.5")
        self.work_key = kw.get("work_key", "friction:41315")


@pytest.fixture
def watch_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr("cursor_bridge.spawn_endpoint_house_ledger.WATCH_DIR", tmp_path)
    return tmp_path


def _folded(watch_dir: Path) -> dict[str, dict[str, Any]]:
    return _fold_spawn_rows(_read_rows(ledger_path(watch_dir)))


def test_pending_row_is_visible_during_the_hold(watch_dir: Path) -> None:
    """(c) registry/ledger reads must see the dispatch while materialize runs."""
    row_id = mint_row_id()
    assert _record_pending_spawn_row(
        row_id=row_id, body=_Body(), work_key="friction:41315"
    )

    rows = _folded(watch_dir)
    assert list(rows) == [row_id]
    row = rows[row_id]
    assert row["state"] == "launching"
    assert row["work_key"] == "friction:41315"
    assert row["model"] == "cursor/composer-2.5"
    assert row["thread"] == "21723"
    assert row["materialize_started_at"].endswith("Z")


def test_materialize_failure_leaves_one_terminal_row(watch_dir: Path) -> None:
    """(a) materialize raises — one row, failed, error class recorded."""
    row_id = mint_row_id()
    _record_pending_spawn_row(row_id=row_id, body=_Body(), work_key=None)

    _fail_pending_spawn_row(
        row_id=row_id, exc=TimeoutError("cortex read"), elapsed_ms=20_000
    )

    rows = _folded(watch_dir)
    assert list(rows) == [row_id], "a dead materialize must not fork a second row"
    row = rows[row_id]
    assert row["state"] == "failed"
    assert row["error_class"] == "TimeoutError"
    assert row["materialize_elapsed_ms"] == 20_000


def test_no_work_key_or_model_omits_those_fields(watch_dir: Path) -> None:
    """Partial failure: a body missing work_key/model still records a row."""
    row_id = mint_row_id()
    body = _Body(model="", work_key=None)
    assert _record_pending_spawn_row(row_id=row_id, body=body, work_key=None)

    row = _folded(watch_dir)[row_id]
    assert row["state"] == "launching"
    assert "work_key" not in row
    assert "model" not in row


def test_duplicate_row_id_is_not_claimed_for_patching(watch_dir: Path) -> None:
    """Wrong ordering: a row_id already in the ledger is not ours to patch."""
    row_id = mint_row_id()
    assert _record_pending_spawn_row(row_id=row_id, body=_Body(), work_key=None)
    assert not _record_pending_spawn_row(row_id=row_id, body=_Body(), work_key=None)
