"""Slice 0 (a:41288, thread 21600 proposal § 3.1): D1, D2, D3 + enum drift guard."""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pytest

from cortex_store.dispatch_ops._friction_close_impl import close_friction_assertion
from cortex_store.dispatch_ops._friction_followon_close import (
    close_spawned_friction_on_todo_done,
)
from cortex_store.dispatch_ops._shared import _FRICTION_CATEGORIES
from cortex_store.dispatch_ops.ops_assertions_update import _op_assertion_get
from cortex_store.dispatch_ops.test_friction_followon_close import (  # noqa: F401
    followon_db,
)
from cortex_store.dispatch_ops.test_friction_to_todo import (  # noqa: F401
    _insert_friction,
    friction_db,
)


@pytest.mark.offline
def test_d1_closeout_friction_files_a_valid_category() -> None:
    from bus_watch.lane_closeout_parse import file_closeout_friction

    seen: dict[str, Any] = {}

    def _fake_file_friction(**kwargs: Any) -> dict[str, Any]:
        seen.update(kwargs)
        return {"item": {"id": 123}}

    with patch("substrate_friction_file.file_friction", _fake_file_friction):
        fid, err = file_closeout_friction(
            worker_text="DEFECT: something broke", lane_id="1", worker_turn=2
        )
    assert err is None and fid == "a:123"
    assert seen["category"] == "regression"
    assert seen["category"] in _FRICTION_CATEGORIES


@pytest.mark.offline
def test_d2_followon_close_does_not_double_prefix_todo(
    followon_db: tuple[int, str],  # noqa: F811
) -> None:
    friction_id, todo_id = followon_db
    from cortex_store import db as cortex_db

    with cortex_db.cortex_conn() as conn:
        result = close_spawned_friction_on_todo_done(
            conn,
            entity_id=todo_id,
            entity_type="todo",
            new_workflow_state="done",
            prior_workflow_state="open",
        )
    assert result is not None and result.get("status") == "closed"
    assert result.get("resolution_kind") == todo_id
    assert not str(result.get("resolution_kind")).startswith("todo:todo:")
    _ = friction_id


@pytest.mark.offline
def test_d3_close_refuses_non_friction_row(friction_db: int) -> None:  # noqa: F811
    from cortex_store import db as cortex_db

    with cortex_db.cortex_conn() as conn:
        plain_id = _insert_friction(conn, "plain observation, not a friction")
    result = close_friction_assertion(
        plain_id, "wontfix", agent="t", session_id="t"
    )
    assert result.get("reason_code") == "not_a_friction_row"
    assert "not_a_friction_row" in str(result.get("error"))
    assert _op_assertion_get(assertion_id=plain_id).get("superseded_by") is None


@pytest.mark.offline
def test_drift_harvest_categories_track_canonical_enum() -> None:
    from bus_watch.friction_rows import HARVEST_CATEGORIES

    assert HARVEST_CATEGORIES == _FRICTION_CATEGORIES - {"feature"}
