"""R7+: park-harvest GIW leg tests (todo:premature-stop-awareness-substrate)."""

from __future__ import annotations

import json
import time
from unittest.mock import AsyncMock

import pytest
from bus_watch.park_harvest import harvest_still_owed

from services.git_integration_worker.cursor_dispatch_ledger import CursorDispatchLedger
from services.git_integration_worker.cursor_sdk_closeout.conductor_hop import (
    maybe_fire_conductor_hop_reactor,
)
from services.git_integration_worker.cursor_sdk_closeout.conductor_park_harvest import (
    build_park_harvest_arm_recipe,
    fire_park_harvest_continue,
    maybe_fire_conductor_park_harvest,
    park_harvest_continue_owed,
    park_harvest_owed,
)
from services.git_integration_worker.cursor_sdk_park import (
    conductor_park_harvest_continue_candidates,
    conductor_park_harvest_watchdog_candidates,
)
from services.git_integration_worker.models.cursor_api import (
    CursorDispatchRequest,
    CursorDispatchResponse,
)

pytestmark = pytest.mark.offline

_WORK_KEY = "todo:park-harvest-fixture"
_OPEN_SCOREBOARD = """\
| G1 | Architecture | DONE |
| G2 | Frame | DONE |
| G3 | Implement | OPEN |
"""

_PARKED_HARVEST_CLOSEOUT = """\
status: complete
stop: PARKED_TRANSPORT
CONSULT_PENDING
execution_id: exec-abc
poll_hint: wait
NEXT_ADMIT: harvest G1
"""

_ROW_HOP_CLOSEOUT = """\
status: complete
stop: ROW_HOP
hop_seq: 1
"""


@pytest.fixture(autouse=True)
def _isolated_ledger(tmp_path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    CursorDispatchLedger._instance = None
    yield tmp_path
    CursorDispatchLedger._instance = None


def _req(**overrides: object) -> CursorDispatchRequest:
    base = {
        "thread_id": "10065",
        "model": "cursor/composer-2.5",
        "dispatch_id": "pred-park-1",
        "execution_id": "exec-pred-park-1",
        "message": "conductor",
    }
    base.update(overrides)
    return CursorDispatchRequest(**base)


def _admit_conductor(
    ledger: CursorDispatchLedger,
    req: CursorDispatchRequest,
    *,
    record_patch: dict | None = None,
) -> None:
    ledger.admit(
        req=req,
        fingerprint=ledger.fingerprint(req),
        execution_id=req.execution_id,
        caller_agent="cursor",
        resolved_model="composer-2.5",
        admission=CursorDispatchResponse(
            admitted=True,
            dispatch_id=req.dispatch_id,
            thread_id=req.thread_id,
            model_id="composer-2.5",
        ),
        contract="light-bounded",
        source_repo="/repo",
        lease_key="/repo",
        work_key=_WORK_KEY,
        source_ref=_WORK_KEY,
        hop_seq=1,
        hop_from="spawn-parent",
        hop_reason="spawn",
    )
    ledger.merge_record_json(
        dispatch_id=req.dispatch_id,
        patch={"packet_kind": "conductor", "lane": "B", "summoning_thread_id": "9638"},
    )
    if record_patch:
        ledger.merge_record_json(dispatch_id=req.dispatch_id, patch=record_patch)


def _terminal_row(
    ledger: CursorDispatchLedger,
    req: CursorDispatchRequest,
    *,
    closeout_body: str,
    closeout_tokens: list[str],
) -> dict:
    ledger.mark_terminal(
        dispatch_id=req.dispatch_id,
        terminal_status="completed",
    )
    ledger.merge_record_json(
        dispatch_id=req.dispatch_id,
        patch={
            "closeout_stop_tokens": closeout_tokens,
            "closeout_body": closeout_body,
        },
    )
    with ledger._connect() as conn:
        row = conn.execute(
            "SELECT * FROM cursor_sdk_dispatches WHERE dispatch_id=?",
            (req.dispatch_id,),
        ).fetchone()
    return {k: row[k] for k in row.keys()}


def test_harvest_still_owed_next_admit_none_false() -> None:
    body = "CONSULT_PENDING\nexecution_id: x\npoll_hint: y\nNEXT_ADMIT: none"
    assert not harvest_still_owed(body=body)


def test_harvest_still_owed_next_admit_harvest_true() -> None:
    body = _PARKED_HARVEST_CLOSEOUT
    assert harvest_still_owed(body=body)


def test_park_harvest_owed_true_on_parked_consult_pending(tmp_path) -> None:
    ledger = CursorDispatchLedger.instance()
    req = _req()
    _admit_conductor(ledger, req)
    row = _terminal_row(
        ledger,
        req,
        closeout_body=_PARKED_HARVEST_CLOSEOUT,
        closeout_tokens=["PARKED_TRANSPORT", "CONSULT_PENDING"],
    )
    assert park_harvest_owed(row, scoreboard_body=_OPEN_SCOREBOARD)


def test_park_harvest_owed_true_on_production_shape_fixture(tmp_path) -> None:
    """L1-3: production-shaped record_json without closeout_body key."""
    ledger = CursorDispatchLedger.instance()
    req = _req()
    _admit_conductor(ledger, req)
    ledger.merge_record_json(
        dispatch_id=req.dispatch_id,
        patch={
            "closeout_stop_tokens": ["PARKED_TRANSPORT", "CONSULT_PENDING"],
            "closeout_turn": 48,
            "closeout_harvest_owed": True,
            "summoning_thread_id": "9638",
        },
    )
    ledger.mark_terminal(dispatch_id=req.dispatch_id, terminal_status="completed")
    with ledger._connect() as conn:
        row = conn.execute(
            "SELECT * FROM cursor_sdk_dispatches WHERE dispatch_id=?",
            (req.dispatch_id,),
        ).fetchone()
    mapped = {k: row[k] for k in row.keys()}
    assert park_harvest_owed(mapped, scoreboard_body=_OPEN_SCOREBOARD)


def test_park_harvest_owed_false_when_row_hop_would_fire(tmp_path) -> None:
    ledger = CursorDispatchLedger.instance()
    req = _req()
    _admit_conductor(ledger, req)
    row = _terminal_row(
        ledger,
        req,
        closeout_body=_ROW_HOP_CLOSEOUT,
        closeout_tokens=["ROW_HOP"],
    )
    assert not park_harvest_owed(row, scoreboard_body=_OPEN_SCOREBOARD)


def test_park_harvest_owed_false_when_hop_parked(tmp_path) -> None:
    """B-7 / N1: budget-park row (hop_parked) must not owe park_harvest."""
    ledger = CursorDispatchLedger.instance()
    req = _req()
    _admit_conductor(ledger, req, record_patch={"hop_parked": True})
    row = _terminal_row(
        ledger,
        req,
        closeout_body=_PARKED_HARVEST_CLOSEOUT,
        closeout_tokens=["PARKED_TRANSPORT", "CONSULT_PENDING"],
    )
    assert not park_harvest_owed(row, scoreboard_body=_OPEN_SCOREBOARD)


def test_build_park_harvest_arm_recipe_uses_record_closeout_turn() -> None:
    """L1-4: arm recipe renders --after-turn from record_json closeout_turn."""
    row = {
        "work_key": _WORK_KEY,
        "record_json": json.dumps({"closeout_turn": 48}),
    }
    body = build_park_harvest_arm_recipe(
        row=row,
        summoning_thread_id="9638",
    )
    assert "--after-turn 48" in body


def test_build_park_harvest_arm_recipe_includes_supervise_start() -> None:
    """B-6 / W3: nudge body includes watch-supervise.sh start arm recipe."""
    row = {
        "work_key": _WORK_KEY,
        "record_json": json.dumps(
            {"scoreboard_uri": "cortex://notes/system/scoreboards/x.md"}
        ),
    }
    body = build_park_harvest_arm_recipe(
        row=row,
        summoning_thread_id="9638",
        closeout_turn=12,
    )
    assert "scripts/watch-supervise.sh start" in body
    assert "scripts/watch-bus-consult-and-page.py" in body
    assert "--thread 9638" in body
    assert "--after-turn 12" in body


@pytest.mark.asyncio
async def test_reactor_fires_park_harvest_not_team_dispatch(tmp_path, monkeypatch):
    ledger = CursorDispatchLedger.instance()
    req = _req()
    _admit_conductor(ledger, req)
    _terminal_row(
        ledger,
        req,
        closeout_body=_PARKED_HARVEST_CLOSEOUT,
        closeout_tokens=["PARKED_TRANSPORT", "CONSULT_PENDING"],
    )

    posted: list[tuple[str, str]] = []
    events: list[str] = []

    def _poster(thread_id: str, body: str) -> None:
        posted.append((thread_id, body))

    monkeypatch.setattr(
        "services.git_integration_worker.cursor_sdk_closeout.conductor_park_harvest.default_park_harvest_poster",
        _poster,
    )
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_sdk_closeout.conductor_park_harvest.emit_frontier_sdk_conductor_hop_park_harvest",
        lambda **_: events.append("park_harvest"),
    )
    hop_post = AsyncMock(return_value=(True, {"dispatch_id": "successor-should-not"}))
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_sdk_closeout.conductor_hop.post_conductor_hop_team_dispatch",
        hop_post,
    )
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_sdk_closeout.conductor_hop.mission_open_for_row",
        lambda *a, **k: True,
    )

    await maybe_fire_conductor_hop_reactor(dispatch_id=req.dispatch_id)

    assert posted
    assert "scripts/watch-supervise.sh start" in posted[0][1]
    assert events == ["park_harvest"]
    hop_post.assert_not_called()
    with ledger._connect() as conn:
        row = conn.execute(
            "SELECT record_json FROM cursor_sdk_dispatches WHERE dispatch_id=?",
            (req.dispatch_id,),
        ).fetchone()
    rec = json.loads(row["record_json"])
    assert "hop_park_harvest_fired_at" in rec
    assert "hop_successor" not in rec


@pytest.mark.asyncio
async def test_park_harvest_idempotent_second_call(tmp_path, monkeypatch):
    ledger = CursorDispatchLedger.instance()
    req = _req()
    _admit_conductor(ledger, req)
    _terminal_row(
        ledger,
        req,
        closeout_body=_PARKED_HARVEST_CLOSEOUT,
        closeout_tokens=["PARKED_TRANSPORT", "CONSULT_PENDING"],
    )

    events: list[str] = []
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_sdk_closeout.conductor_park_harvest.default_park_harvest_poster",
        lambda *a, **k: None,
    )
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_sdk_closeout.conductor_park_harvest.emit_frontier_sdk_conductor_hop_park_harvest",
        lambda **_: events.append("park_harvest"),
    )
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_sdk_closeout.conductor_park_harvest.mission_open",
        lambda **_: True,
    )

    assert await maybe_fire_conductor_park_harvest(dispatch_id=req.dispatch_id)
    assert events == ["park_harvest"]
    assert not await maybe_fire_conductor_park_harvest(dispatch_id=req.dispatch_id)
    assert events == ["park_harvest"]


@pytest.mark.asyncio
async def test_park_harvest_watchdog_retries_unstamped_row(tmp_path, monkeypatch):
    """B-4: watchdog sweep selects unstamped park_harvest rows past grace."""
    ledger = CursorDispatchLedger.instance()
    req = _req()
    _admit_conductor(ledger, req)
    _terminal_row(
        ledger,
        req,
        closeout_body=_PARKED_HARVEST_CLOSEOUT,
        closeout_tokens=["PARKED_TRANSPORT", "CONSULT_PENDING"],
    )
    ledger.merge_record_json(
        dispatch_id=req.dispatch_id,
        patch={"hop_last_terminal_at": time.time() - 300.0},
    )
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_sdk_closeout.conductor_park_harvest.mission_open",
        lambda **_: True,
    )
    assert conductor_park_harvest_watchdog_candidates(ledger, grace_s=120.0) == [
        req.dispatch_id
    ]


def _production_parked_row(
    ledger: CursorDispatchLedger,
    req: CursorDispatchRequest,
    *,
    closeout_turn: int = 48,
    harvest_owed: bool = True,
    fired: bool = True,
) -> dict:
    _admit_conductor(ledger, req)
    patch: dict = {
        "closeout_stop_tokens": ["PARKED_TRANSPORT", "CONSULT_PENDING"],
        "closeout_turn": closeout_turn,
        "closeout_harvest_owed": harvest_owed,
        "summoning_thread_id": "9638",
    }
    if fired:
        patch["hop_park_harvest_fired_at"] = time.time()
    ledger.merge_record_json(dispatch_id=req.dispatch_id, patch=patch)
    ledger.mark_terminal(dispatch_id=req.dispatch_id, terminal_status="completed")
    with ledger._connect() as conn:
        row = conn.execute(
            "SELECT * FROM cursor_sdk_dispatches WHERE dispatch_id=?",
            (req.dispatch_id,),
        ).fetchone()
    return {k: row[k] for k in row.keys()}


def test_park_harvest_continue_owed_true_when_reply_arrived() -> None:
    """R-1: full conjunction with snapshot mock complete."""
    ledger = CursorDispatchLedger.instance()
    req = _req()
    row = _production_parked_row(ledger, req)
    assert park_harvest_continue_owed(
        row,
        reply_fn=lambda *_a, **_k: True,
    )


def test_park_harvest_continue_owed_false_when_row_pinned() -> None:
    """R-2: operator stop tokens must not trigger continue."""
    ledger = CursorDispatchLedger.instance()
    req = _req()
    row = _production_parked_row(ledger, req)
    row["record_json"] = json.dumps(
        {
            **json.loads(row["record_json"]),
            "closeout_stop_tokens": ["ROW_PINNED"],
            "hop_park_harvest_fired_at": time.time(),
            "closeout_turn": 48,
        }
    )
    assert not park_harvest_continue_owed(row, reply_fn=lambda *_a, **_k: True)


def test_park_harvest_continue_owed_false_when_hop_parked() -> None:
    """R-3: budget park rows excluded."""
    ledger = CursorDispatchLedger.instance()
    req = _req()
    row = _production_parked_row(ledger, req)
    row["record_json"] = json.dumps(
        {**json.loads(row["record_json"]), "hop_parked": True}
    )
    assert not park_harvest_continue_owed(row, reply_fn=lambda *_a, **_k: True)


def test_park_harvest_continue_owed_false_when_snapshot_incomplete() -> None:
    """R-4: incomplete snapshot ⇒ False."""
    ledger = CursorDispatchLedger.instance()
    req = _req()
    row = _production_parked_row(ledger, req)
    assert not park_harvest_continue_owed(row, reply_fn=lambda *_a, **_k: False)


@pytest.mark.asyncio
async def test_fire_park_harvest_continue_posts_and_stamps(tmp_path, monkeypatch):
    """R-5/R-6: POST with hop_reason park_harvest; idempotent second sweep."""
    ledger = CursorDispatchLedger.instance()
    req = _req()
    _production_parked_row(ledger, req)

    captured: dict = {}

    async def _capture(body, **_kwargs):
        captured["body"] = body
        return True, {"dispatch_id": "succ-park-harvest-1"}

    monkeypatch.setattr(
        "services.git_integration_worker.cursor_sdk_closeout.conductor_hop.post_conductor_hop_team_dispatch",
        _capture,
    )

    with ledger._connect() as conn:
        row = conn.execute(
            "SELECT * FROM cursor_sdk_dispatches WHERE dispatch_id=?",
            (req.dispatch_id,),
        ).fetchone()
    mapped = {k: row[k] for k in row.keys()}

    assert await fire_park_harvest_continue(mapped)
    assert captured["body"]["hop_reason"] == "park_harvest"
    assert captured["body"]["reuse_thread"] == req.thread_id
    assert (
        captured["body"]["generation_options"]["idempotency_key"]
        == f"conductor-hop:{req.dispatch_id}"
    )
    with ledger._connect() as conn:
        row = conn.execute(
            "SELECT record_json FROM cursor_sdk_dispatches WHERE dispatch_id=?",
            (req.dispatch_id,),
        ).fetchone()
    rec = json.loads(row["record_json"])
    assert rec.get("hop_successor") == "succ-park-harvest-1"
    assert "hop_park_harvest_continued_at" in rec
    assert "hop_parked" not in rec or rec.get("hop_parked") is not True

    assert not park_harvest_continue_owed(
        {**mapped, "record_json": row["record_json"]},
        reply_fn=lambda *_a, **_k: True,
    )


def test_park_harvest_continue_candidates_no_grace() -> None:
    """R-B2 candidates: no grace delay for continue class."""
    ledger = CursorDispatchLedger.instance()
    req = _req()
    _production_parked_row(ledger, req)
    assert conductor_park_harvest_continue_candidates(ledger) == [req.dispatch_id]


@pytest.mark.asyncio
async def test_fire_park_harvest_failed_post_leaves_row_retryable(tmp_path, monkeypatch):
    """F2: failed POST must not stamp hop_park_harvest_fired_at; watchdog retries."""
    ledger = CursorDispatchLedger.instance()
    req = _req()
    _admit_conductor(ledger, req)
    _terminal_row(
        ledger,
        req,
        closeout_body=_PARKED_HARVEST_CLOSEOUT,
        closeout_tokens=["PARKED_TRANSPORT", "CONSULT_PENDING"],
    )
    ledger.merge_record_json(
        dispatch_id=req.dispatch_id,
        patch={"hop_last_terminal_at": time.time() - 300.0},
    )

    def _raise_post(*_a, **_k):
        raise RuntimeError("bus post failed")

    monkeypatch.setattr(
        "services.git_integration_worker.cursor_sdk_closeout.conductor_park_harvest.default_park_harvest_poster",
        _raise_post,
    )
    events: list[str] = []
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_sdk_closeout.conductor_park_harvest.emit_frontier_sdk_conductor_hop_park_harvest",
        lambda **_: events.append("park_harvest"),
    )
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_sdk_closeout.conductor_park_harvest.mission_open",
        lambda **_: True,
    )

    assert not await maybe_fire_conductor_park_harvest(dispatch_id=req.dispatch_id)
    assert events == []
    with ledger._connect() as conn:
        row = conn.execute(
            "SELECT record_json FROM cursor_sdk_dispatches WHERE dispatch_id=?",
            (req.dispatch_id,),
        ).fetchone()
    rec = json.loads(row["record_json"])
    assert "hop_park_harvest_fired_at" not in rec
    assert conductor_park_harvest_watchdog_candidates(ledger, grace_s=120.0) == [
        req.dispatch_id
    ]
