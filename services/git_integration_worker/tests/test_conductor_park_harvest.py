"""R7+: park-harvest GIW leg tests (todo:premature-stop-awareness-substrate)."""

from __future__ import annotations

import inspect
import json
import os
import pwd
import time
import uuid
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from bus_watch.park_harvest import harvest_still_owed

from services.git_integration_worker.cursor_dispatch_ledger import (
    CURSOR_SDK_DISPATCH_LEDGER_ENV,
    CursorDispatchLedger,
    resolve_cursor_sdk_dispatch_ledger_path,
)
from services.git_integration_worker.cursor_sdk_closeout.conductor_hop import (
    maybe_fire_conductor_hop_reactor,
)
from services.git_integration_worker.cursor_sdk_closeout.conductor_park_harvest import (
    _evaluate_harvest_reply_execution,
    _park_harvest_target_token,
    _reply_matches_harvest_execution,
    build_park_harvest_arm_recipe,
    fire_park_harvest,
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

_HARVEST_REPLY_UUID = "994dc2b4-d1dd-4206-bd97-2ad0109478bc"


def _stub_shape_b_reply_match() -> patch:
    """Hermetic stand-in for a matching CDP reply header (shape B)."""
    return patch(
        "services.git_integration_worker.cursor_sdk_closeout.conductor_park_harvest."
        "_evaluate_harvest_reply_execution",
        return_value=(True, None),
    )

_ROW_HOP_CLOSEOUT = """\
status: complete
stop: ROW_HOP
hop_seq: 1
"""


@pytest.fixture(autouse=True)
def _isolated_ledger(tmp_path, monkeypatch: pytest.MonkeyPatch):
    from services.git_integration_worker.seat_write_ledger import SeatWriteLedger

    root = tmp_path / "giw-ledger-root"
    root.mkdir(parents=True, exist_ok=True)
    isolated_db = root / "cursor-sdk-dispatch.db"
    monkeypatch.setenv("DATA_DIR", str(root))
    monkeypatch.setenv(CURSOR_SDK_DISPATCH_LEDGER_ENV, str(isolated_db))
    SeatWriteLedger.reset_instance()
    CursorDispatchLedger._instance = None
    yield root
    SeatWriteLedger.reset_instance()
    CursorDispatchLedger._instance = None


def _req(**overrides: object) -> CursorDispatchRequest:
    dispatch_id = str(overrides.get("dispatch_id", uuid.uuid4().hex[:20]))
    execution_id = str(overrides.get("execution_id", f"exec-{dispatch_id}"))
    base = {
        "thread_id": "10065",
        "model": "cursor/composer-2.5",
        "dispatch_id": dispatch_id,
        "execution_id": execution_id,
        "message": "conductor",
    }
    base.update(overrides)
    return CursorDispatchRequest(**base)


def test_park_harvest_module_pins_dispatch_ledger_away_from_live_gateway() -> None:
    """Module autouse must pin CURSOR_SDK_DISPATCH_LEDGER so bridge export cannot win."""
    pinned = os.environ.get(CURSOR_SDK_DISPATCH_LEDGER_ENV, "").strip()
    assert pinned, "module autouse must set CURSOR_SDK_DISPATCH_LEDGER"
    resolved = resolve_cursor_sdk_dispatch_ledger_path()
    live_gateway = (Path(pwd.getpwuid(os.getuid()).pw_dir) / ".gateway").resolve()
    assert resolved == Path(pinned).resolve()
    assert resolved != (live_gateway / "cursor-sdk-dispatch.db").resolve()
    assert not resolved.is_relative_to(live_gateway)


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
        contract="conductor",
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
        patch={"contract": "conductor", "lane": "B", "summoning_thread_id": "9638"},
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


def test_park_harvest_owed_body_overrides_false_stamp(tmp_path) -> None:
    """Stale closeout_harvest_owed=false must not block PARKED_TRANSPORT harvest."""
    ledger = CursorDispatchLedger.instance()
    req = _req()
    _admit_conductor(ledger, req)
    omitted_next = """\
status: complete
stop: PARKED_TRANSPORT
CONSULT_PENDING
execution_id: exec-abc
poll_hint: wait
"""
    _terminal_row(
        ledger,
        req,
        closeout_body=omitted_next,
        closeout_tokens=["PARKED_TRANSPORT", "CONSULT_PENDING"],
    )
    ledger.merge_record_json(
        dispatch_id=req.dispatch_id,
        patch={"closeout_harvest_owed": False},
    )
    with ledger._connect() as conn:
        refreshed = conn.execute(
            "SELECT * FROM cursor_sdk_dispatches WHERE dispatch_id=?",
            (req.dispatch_id,),
        ).fetchone()
    mapped = {k: refreshed[k] for k in refreshed.keys()}
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


def test_build_park_harvest_arm_recipe_push_wake_only() -> None:
    body = build_park_harvest_arm_recipe(
        row={"work_key": "todo:x"},
        summoning_thread_id="18374",
        push_wake_only=True,
    )
    assert "do not arm watch-supervise.sh" in body
    assert "watch-bus-consult-and-page.py" in body
    assert "display-chat-wake" in body
    assert "BRIDGE_WAKE" in body
    assert "start --label" not in body


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
    # a:33486 — a producer-less poller cannot stall-pop, so one of these is required.
    assert ("--execution-id " in body) or ("--no-producer" in body)
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

    events: list[str] = []
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


_LIVE_CDP = "642fe99c-45e4-487d-9abb-132c1023a679"
_FENCED_PARK_HARVEST_CLOSEOUT = f"""\
status: complete
```
stop: PARKED_TRANSPORT
```
NEXT_ADMIT: harvest {_LIVE_CDP}
"""


@pytest.mark.asyncio
async def test_fenced_park_harvest_owed_wakes_once_and_parked_transport_does_not_double(
    tmp_path, monkeypatch
):
    """conductor_park_harvest_owed with an in-flight CDP id stamps once (no bus wake).

    Stored tokens omit PARKED_TRANSPORT (the stop line is fenced), which is
    the next_admit_blocked strand. A designed PARKED_TRANSPORT stop still
    stamps exactly once per row.
    """
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_sdk_closeout.conductor_park_harvest.emit_frontier_sdk_conductor_hop_park_harvest",
        lambda **_: None,
    )
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_sdk_closeout.conductor_hop.mission_open_for_row",
        lambda *a, **k: True,
    )
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_sdk_closeout.conductor_hop.post_conductor_hop_team_dispatch",
        AsyncMock(return_value=(True, {"dispatch_id": "successor-should-not"})),
    )

    ledger = CursorDispatchLedger.instance()
    fenced = _req(dispatch_id="pred-park-fenced", execution_id=_LIVE_CDP)
    _admit_conductor(
        ledger, fenced, record_patch={"cdp_execution_id": _LIVE_CDP}
    )
    _terminal_row(
        ledger,
        fenced,
        closeout_body=_FENCED_PARK_HARVEST_CLOSEOUT,
        closeout_tokens=[],
    )
    await maybe_fire_conductor_hop_reactor(dispatch_id=fenced.dispatch_id)
    with ledger._connect() as conn:
        fenced_rec = json.loads(
            conn.execute(
                "SELECT record_json FROM cursor_sdk_dispatches WHERE dispatch_id=?",
                (fenced.dispatch_id,),
            ).fetchone()["record_json"]
        )
    assert "hop_park_harvest_fired_at" in fenced_rec

    designed = _req(dispatch_id="pred-park-designed")
    _admit_conductor(ledger, designed)
    _terminal_row(
        ledger,
        designed,
        closeout_body=(
            "status: complete\n"
            "stop: PARKED_TRANSPORT\n"
            f"NEXT_ADMIT: harvest {_HARVEST_REPLY_UUID}\n"
        ),
        closeout_tokens=["PARKED_TRANSPORT"],
    )
    await maybe_fire_conductor_hop_reactor(dispatch_id=designed.dispatch_id)
    with ledger._connect() as conn:
        designed_rec = json.loads(
            conn.execute(
                "SELECT record_json FROM cursor_sdk_dispatches WHERE dispatch_id=?",
                (designed.dispatch_id,),
            ).fetchone()["record_json"]
        )
    assert "hop_park_harvest_fired_at" in designed_rec

    ledger.merge_record_json(
        dispatch_id=fenced.dispatch_id, patch={"closeout_turn": 48}
    )
    ledger.merge_record_json(
        dispatch_id=designed.dispatch_id, patch={"closeout_turn": 48}
    )
    admits: list[dict] = []

    async def _admit(body, **_k):
        admits.append(body)
        return True, {"dispatch_id": f"succ-{len(admits)}"}

    monkeypatch.setattr(
        "services.git_integration_worker.cursor_sdk_closeout.conductor_hop.post_conductor_hop_team_dispatch",
        _admit,
    )

    def _load(dispatch_id: str) -> dict:
        with ledger._connect() as conn:
            row = conn.execute(
                "SELECT * FROM cursor_sdk_dispatches WHERE dispatch_id=?",
                (dispatch_id,),
            ).fetchone()
        return {k: row[k] for k in row.keys()}

    def _reply(*_a, **_k):
        return True

    fenced_row = _load(fenced.dispatch_id)
    with _stub_shape_b_reply_match():
        assert park_harvest_continue_owed(fenced_row, reply_fn=_reply)
        assert await fire_park_harvest_continue(fenced_row)
    assert len(admits) == 1
    assert admits[0]["hop_reason"] == "park_harvest"
    assert not park_harvest_continue_owed(_load(fenced.dispatch_id), reply_fn=_reply)

    designed_row = _load(designed.dispatch_id)
    with _stub_shape_b_reply_match():
        assert park_harvest_continue_owed(designed_row, reply_fn=_reply)
        assert await fire_park_harvest_continue(designed_row)
    assert len(admits) == 2
    assert admits[1]["hop_reason"] == "park_harvest"

    row_hop = _req(dispatch_id="pred-park-row-hop")
    _admit_conductor(ledger, row_hop)
    _terminal_row(
        ledger,
        row_hop,
        closeout_body=(
            "status: complete\n"
            "stop: ROW_HOP\n"
            "```\n"
            "stop: PARKED_TRANSPORT\n"
            "```\n"
            f"NEXT_ADMIT: harvest {_LIVE_CDP}\n"
        ),
        closeout_tokens=[],
    )
    ledger.merge_record_json(
        dispatch_id=row_hop.dispatch_id, patch={"closeout_turn": 48}
    )
    assert not park_harvest_continue_owed(_load(row_hop.dispatch_id), reply_fn=_reply)
    assert len(admits) == 2


@pytest.mark.asyncio
async def test_routine_park_harvest_stamps_without_default_poster(
    tmp_path, monkeypatch
):
    """Production path (poster=None) emits event + stamp, no park-harvest wake bus turn."""
    ledger = CursorDispatchLedger.instance()
    req = _req()
    _admit_conductor(ledger, req)
    row = _terminal_row(
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

    assert await fire_park_harvest(row)
    assert posted == []
    assert events == ["park_harvest"]
    with ledger._connect() as conn:
        rec_row = conn.execute(
            "SELECT record_json FROM cursor_sdk_dispatches WHERE dispatch_id=?",
            (req.dispatch_id,),
        ).fetchone()
    assert "hop_park_harvest_fired_at" in json.loads(rec_row["record_json"])

    assert await fire_park_harvest(row, poster=_poster)
    assert len(posted) == 1
    assert "watch-supervise.sh" in posted[0][1]


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
        "closeout_body": f"NEXT_ADMIT: harvest {_HARVEST_REPLY_UUID}\n",
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


def test_park_harvest_reply_on_worker_thread_readmits_without_cursor_post() -> None:
    """R1: harvest owed at terminal without fired stamp still continues on reply."""
    ledger = CursorDispatchLedger.instance()
    req = _req()
    row = _production_parked_row(ledger, req, fired=False)
    with _stub_shape_b_reply_match():
        assert park_harvest_continue_owed(
            row,
            reply_fn=lambda *_a, **_k: True,
        )
    assert conductor_park_harvest_continue_candidates(ledger) == [req.dispatch_id]


def test_park_harvest_continue_owed_true_when_reply_arrived() -> None:
    """R-1: full conjunction with snapshot mock complete."""
    ledger = CursorDispatchLedger.instance()
    req = _req()
    row = _production_parked_row(ledger, req)
    with _stub_shape_b_reply_match():
        assert park_harvest_continue_owed(
            row,
            reply_fn=lambda *_a, **_k: True,
        )


def test_park_harvest_continue_owed_unchanged_for_live_harvest_id() -> None:
    """Continue reads ledger/stop/body/hop keys, not the park grade.

    A live harvest uuid and a non-id ``harvest G1`` body both still continue.
    """
    source = inspect.getsource(park_harvest_continue_owed)
    assert "conductor_park_harvest_owed" not in source
    assert "status_incomplete_class" not in source
    assert "work_outcome" not in source
    ledger = CursorDispatchLedger.instance()
    req = _req(dispatch_id="park-live-grade")
    row = _production_parked_row(ledger, req)
    record = json.loads(row["record_json"])
    live = dict(record)
    live["closeout_body"] = (
        "stop: PARKED_TRANSPORT\n"
        "NEXT_ADMIT: harvest 642fe99c-45e4-487d-9abb-132c1023a679\n"
    )
    g1 = dict(record)
    g1["closeout_body"] = _PARKED_HARVEST_CLOSEOUT
    row_live = dict(row)
    row_g1 = dict(row)
    row_live["record_json"] = json.dumps(live)
    row_g1["record_json"] = json.dumps(g1)

    def _reply(*_a, **_k):
        return True

    with _stub_shape_b_reply_match():
        assert park_harvest_continue_owed(row_live, reply_fn=_reply)
        assert not park_harvest_continue_owed(row_g1, reply_fn=_reply)


def test_park_harvest_continue_rejects_done_in_body() -> None:
    """Park-harvest continue does not accept a body that also says stop: DONE."""
    ledger = CursorDispatchLedger.instance()
    req = _req(dispatch_id="park-done-body")
    row = _production_parked_row(ledger, req)
    record = json.loads(row["record_json"])
    record["closeout_body"] = "stop: PARKED_TRANSPORT\nstop: DONE\n"
    row["record_json"] = json.dumps(record)
    with patch(
        "services.git_integration_worker.cursor_sdk_closeout.conductor_park_harvest."
        "emit_frontier_sdk_conductor_hop_park_harvest_continue_refused"
    ) as emit_refused:
        assert not park_harvest_continue_owed(row, reply_fn=lambda *_a, **_k: True)
        emit_refused.assert_not_called()


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


@pytest.mark.asyncio
async def test_fire_park_harvest_continue_admits_when_harvest_absent(
    monkeypatch,
):
    """a:38115: NEXT_ADMIT harvest gone from the registry still admits.

    hop_body_build_refused stays true on PARKED_TRANSPORT (no ROW_HOP lift).
    Continue must skip that refuse after owed already saw the reply.
    """
    harvest_id = "c06ef180-7ee9-47f3-b6ed-50f0421b4ec4"
    closeout = (
        "status: complete\n"
        "stop: PARKED_TRANSPORT\n"
        "CONSULT_PENDING\n"
        f"execution_id: {harvest_id}\n"
        "poll_hint: wait\n"
        f"NEXT_ADMIT: harvest {harvest_id}\n"
    )
    ledger = CursorDispatchLedger.instance()
    req = _req(dispatch_id="c5fe47561ce9-6ae01aaa", thread_id="15243")
    _admit_conductor(ledger, req)
    ledger.merge_record_json(
        dispatch_id=req.dispatch_id,
        patch={
            "closeout_stop_tokens": ["PARKED_TRANSPORT", "CONSULT_PENDING"],
            "closeout_turn": 9,
            "closeout_harvest_owed": True,
            "closeout_body": closeout,
            "hop_next_admit": f"harvest {harvest_id}",
            "hop_park_harvest_fired_at": time.time(),
            "summoning_thread_id": "15243",
        },
    )
    ledger.mark_terminal(dispatch_id=req.dispatch_id, terminal_status="completed")
    with ledger._connect() as conn:
        row = conn.execute(
            "SELECT * FROM cursor_sdk_dispatches WHERE dispatch_id=?",
            (req.dispatch_id,),
        ).fetchone()
    mapped = {k: row[k] for k in row.keys()}

    captured: dict = {}

    async def _capture(body, **_kwargs):
        captured["body"] = body
        return True, {"dispatch_id": "b0818439b9f4-e339e808"}

    monkeypatch.setattr(
        "services.git_integration_worker.cursor_sdk_closeout.conductor_hop.post_conductor_hop_team_dispatch",
        _capture,
    )
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_sdk_closeout.conductor_hop._named_target_is_terminal",
        lambda *_a, **_k: False,
    )
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_sdk_closeout.conductor_hop._collect_dispatch_targets",
        lambda *_a, **_k: [],
    )
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_sdk_closeout.conductor_hop._collect_execution_targets",
        lambda *_a, **_k: [],
    )

    from services.git_integration_worker.cursor_sdk_closeout.conductor_hop import (
        build_hop_team_dispatch_body,
        hop_body_build_refused,
    )

    rec = json.loads(mapped["record_json"])
    assert hop_body_build_refused(mapped, rec)
    assert build_hop_team_dispatch_body(mapped) is None
    assert await fire_park_harvest_continue(mapped)
    assert captured["body"]["hop_reason"] == "park_harvest"
    with ledger._connect() as conn:
        stamped = conn.execute(
            "SELECT record_json FROM cursor_sdk_dispatches WHERE dispatch_id=?",
            (req.dispatch_id,),
        ).fetchone()
    rec = json.loads(stamped["record_json"])
    assert rec.get("hop_successor") == "b0818439b9f4-e339e808"
    assert "hop_park_harvest_continued_at" in rec


def test_park_harvest_continue_candidates_no_grace() -> None:
    """R-B2 candidates: no grace delay for continue class."""
    ledger = CursorDispatchLedger.instance()
    req = _req()
    _production_parked_row(ledger, req)
    assert conductor_park_harvest_continue_candidates(ledger) == [req.dispatch_id]


def test_park_harvest_continue_candidates_fenced_body_grade() -> None:
    """R-B2 selector: fenced park-harvest row is a continue candidate without stored token."""
    ledger = CursorDispatchLedger.instance()
    fenced = _req(dispatch_id="sel-fenced-park", thread_id="10066")
    _admit_conductor(ledger, fenced, record_patch={"cdp_execution_id": _LIVE_CDP})
    _terminal_row(
        ledger,
        fenced,
        closeout_body=_FENCED_PARK_HARVEST_CLOSEOUT,
        closeout_tokens=[],
    )
    ledger.merge_record_json(
        dispatch_id=fenced.dispatch_id,
        patch={"hop_park_harvest_fired_at": time.time()},
    )
    designed = _req(dispatch_id="sel-designed-park", thread_id="10067")
    _production_parked_row(ledger, designed)
    row_hop = _req(dispatch_id="sel-row-hop-park", thread_id="10068")
    _admit_conductor(ledger, row_hop)
    _terminal_row(
        ledger,
        row_hop,
        closeout_body=(
            "status: complete\n"
            "stop: ROW_HOP\n"
            "```\n"
            "stop: PARKED_TRANSPORT\n"
            "```\n"
            f"NEXT_ADMIT: harvest {_LIVE_CDP}\n"
        ),
        closeout_tokens=[],
    )
    ledger.merge_record_json(
        dispatch_id=row_hop.dispatch_id,
        patch={"hop_park_harvest_fired_at": time.time()},
    )

    candidates = conductor_park_harvest_continue_candidates(ledger)
    assert candidates.count(fenced.dispatch_id) == 1
    assert candidates.count(designed.dispatch_id) == 1
    assert row_hop.dispatch_id not in candidates


def test_park_harvest_owed_body_park_live_sibling_parity_with_designed(
    monkeypatch,
) -> None:
    """Body-grade wake and stored PARKED_TRANSPORT agree when a live sibling exists."""
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_sdk_closeout.conductor_hop.live_conductor_row_on_thread",
        lambda **_k: True,
    )
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_sdk_closeout.conductor_hop.conductor_has_live_nested",
        lambda **_k: True,
    )
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_sdk_closeout.conductor_park_harvest.mission_open",
        lambda **_: True,
    )

    ledger = CursorDispatchLedger.instance()
    fenced = _req(dispatch_id="parity-fenced")
    _admit_conductor(ledger, fenced, record_patch={"cdp_execution_id": _LIVE_CDP})
    _terminal_row(
        ledger,
        fenced,
        closeout_body=_FENCED_PARK_HARVEST_CLOSEOUT,
        closeout_tokens=[],
    )
    designed = _req(dispatch_id="parity-designed")
    _admit_conductor(ledger, designed)
    _terminal_row(
        ledger,
        designed,
        closeout_body=_PARKED_HARVEST_CLOSEOUT,
        closeout_tokens=["PARKED_TRANSPORT", "CONSULT_PENDING"],
    )

    with ledger._connect() as conn:
        fenced_row = conn.execute(
            "SELECT * FROM cursor_sdk_dispatches WHERE dispatch_id=?",
            (fenced.dispatch_id,),
        ).fetchone()
        designed_row = conn.execute(
            "SELECT * FROM cursor_sdk_dispatches WHERE dispatch_id=?",
            (designed.dispatch_id,),
        ).fetchone()
    fenced_mapped = {k: fenced_row[k] for k in fenced_row.keys()}
    designed_mapped = {k: designed_row[k] for k in designed_row.keys()}

    fenced_owed = park_harvest_owed(fenced_mapped, scoreboard_body=_OPEN_SCOREBOARD)
    designed_owed = park_harvest_owed(designed_mapped, scoreboard_body=_OPEN_SCOREBOARD)
    assert fenced_owed == designed_owed


@pytest.mark.asyncio
async def test_fire_park_harvest_failed_post_leaves_row_retryable(
    tmp_path, monkeypatch
):
    """F2: explicit poster failure must not stamp hop_park_harvest_fired_at."""
    ledger = CursorDispatchLedger.instance()
    req = _req()
    _admit_conductor(ledger, req)
    row = _terminal_row(
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

    events: list[str] = []
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_sdk_closeout.conductor_park_harvest.emit_frontier_sdk_conductor_hop_park_harvest",
        lambda **_: events.append("park_harvest"),
    )
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_sdk_closeout.conductor_park_harvest.mission_open",
        lambda **_: True,
    )

    assert not await fire_park_harvest(row, poster=_raise_post)
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


def _reload_row(ledger: CursorDispatchLedger, dispatch_id: str) -> dict:
    with ledger._connect() as conn:
        row = conn.execute(
            "SELECT * FROM cursor_sdk_dispatches WHERE dispatch_id=?",
            (dispatch_id,),
        ).fetchone()
    return {k: row[k] for k in row.keys()}


@pytest.mark.asyncio
async def test_park_harvest_continue_stop_not_claimed_not_retried_a_third_time(
    monkeypatch,
) -> None:
    """Two stop_not_claimed admits cap the path; third fire does not POST."""
    ledger = CursorDispatchLedger.instance()
    req = _req()
    _production_parked_row(ledger, req)
    stop_id = req.dispatch_id
    post_calls = 0

    async def _stop_not_claimed(_body, **_kwargs):
        nonlocal post_calls
        post_calls += 1
        return False, {"reason": "stop_not_claimed", "stop_id": stop_id}

    monkeypatch.setattr(
        "services.git_integration_worker.cursor_sdk_closeout.conductor_hop.post_conductor_hop_team_dispatch",
        _stop_not_claimed,
    )

    row = _reload_row(ledger, req.dispatch_id)
    with _stub_shape_b_reply_match():
        assert park_harvest_continue_owed(row, reply_fn=lambda *_a, **_k: True)

        assert await fire_park_harvest_continue(row) is False
    assert post_calls == 1
    row = _reload_row(ledger, req.dispatch_id)
    rec = json.loads(row["record_json"])
    assert "hop_park_harvest_continued_at" not in rec
    err = rec["hop_admit_error"]
    assert err["retryable"] is True
    assert err["attempts"] == 1
    assert err.get("stop_not_claimed_attempts") == 1
    assert err.get("reason") == "stop_not_claimed"
    assert "stop_not_claimed" in err["last_error"]

    assert await fire_park_harvest_continue(row) is False
    assert post_calls == 2
    row = _reload_row(ledger, req.dispatch_id)
    rec = json.loads(row["record_json"])
    err = rec["hop_admit_error"]
    assert err["retryable"] is False
    assert err["attempts"] >= 2
    assert err.get("stop_not_claimed_attempts") == 2
    assert "hop_park_harvest_continued_at" not in rec

    assert await fire_park_harvest_continue(row) is False
    assert post_calls == 2
    with _stub_shape_b_reply_match():
        assert not park_harvest_continue_owed(row, reply_fn=lambda *_a, **_k: True)
    assert conductor_park_harvest_continue_candidates(ledger) == []


@pytest.mark.asyncio
async def test_park_harvest_continue_statusless_transport_still_retries(
    monkeypatch,
) -> None:
    """Status-less transport failures stay retryable; cap does not apply."""
    ledger = CursorDispatchLedger.instance()
    req = _req()
    _production_parked_row(ledger, req)
    post_calls = 0

    async def _unreachable(_body, **_kwargs):
        nonlocal post_calls
        post_calls += 1
        return False, {"error": "ConnectError", "reason": "stargate_unreachable"}

    monkeypatch.setattr(
        "services.git_integration_worker.cursor_sdk_closeout.conductor_hop.post_conductor_hop_team_dispatch",
        _unreachable,
    )

    row = _reload_row(ledger, req.dispatch_id)
    for _ in range(3):
        assert await fire_park_harvest_continue(row) is False
        row = _reload_row(ledger, req.dispatch_id)
    assert post_calls == 3
    err = json.loads(row["record_json"])["hop_admit_error"]
    assert err["retryable"] is True
    assert err.get("stop_not_claimed_attempts") in (None, 0)
    assert err["attempts"] == 3


@pytest.mark.asyncio
async def test_park_harvest_transport_then_stop_not_claimed_still_retries(
    monkeypatch,
) -> None:
    """One transport failure then stop_not_claimed still POSTs a third time."""
    ledger = CursorDispatchLedger.instance()
    req = _req()
    _production_parked_row(ledger, req)
    post_calls = 0
    responses = [
        {"error": "ConnectError", "reason": "stargate_unreachable"},
        {"reason": "stop_not_claimed", "stop_id": req.dispatch_id},
    ]

    async def _sequenced(_body, **_kwargs):
        nonlocal post_calls
        post_calls += 1
        if post_calls == 1:
            detail = responses[0]
        elif post_calls == 2:
            detail = responses[1]
        else:
            detail = {"error": "ConnectError", "reason": "stargate_unreachable"}
        return False, detail

    monkeypatch.setattr(
        "services.git_integration_worker.cursor_sdk_closeout.conductor_hop.post_conductor_hop_team_dispatch",
        _sequenced,
    )

    row = _reload_row(ledger, req.dispatch_id)
    assert await fire_park_harvest_continue(row) is False
    row = _reload_row(ledger, req.dispatch_id)
    assert await fire_park_harvest_continue(row) is False
    row = _reload_row(ledger, req.dispatch_id)
    with _stub_shape_b_reply_match():
        assert park_harvest_continue_owed(row, reply_fn=lambda *_a, **_k: True)
        assert await fire_park_harvest_continue(row) is False
    assert post_calls == 3
    row = _reload_row(ledger, req.dispatch_id)
    err = json.loads(row["record_json"])["hop_admit_error"]
    assert err["attempts"] == 3
    assert err.get("stop_not_claimed_attempts") == 1
    assert err["retryable"] is True


@pytest.mark.asyncio
async def test_park_harvest_stop_not_claimed_legacy_last_error_only(
    monkeypatch,
) -> None:
    """Pre-counter rows: exhaustion from attempts + last_error substring only."""
    ledger = CursorDispatchLedger.instance()
    req = _req()
    _production_parked_row(ledger, req)
    ledger.merge_record_json(
        dispatch_id=req.dispatch_id,
        patch={
            "hop_admit_error": {
                "retryable": True,
                "attempts": 2,
                "last_status_code": 409,
                "last_error": '{"reason": "stop_not_claimed"}',
            }
        },
    )
    post_calls = 0

    async def _unexpected(_body, **_kwargs):
        nonlocal post_calls
        post_calls += 1
        return False, {"reason": "stop_not_claimed"}

    monkeypatch.setattr(
        "services.git_integration_worker.cursor_sdk_closeout.conductor_hop.post_conductor_hop_team_dispatch",
        _unexpected,
    )

    row = _reload_row(ledger, req.dispatch_id)
    assert not park_harvest_continue_owed(row, reply_fn=lambda *_a, **_k: True)
    assert await fire_park_harvest_continue(row) is False
    assert post_calls == 0


def test_park_harvest_admit_error_increment_race_safe() -> None:
    """Stale snapshot merge under-counts; conn merge reads fresh row each time."""
    from services.git_integration_worker.cursor_sdk_ledger_hop import (
        merge_hop_admit_error_into_record_json_conn,
        merge_hop_patch,
    )

    ledger = CursorDispatchLedger.instance()
    req = _req()
    _production_parked_row(ledger, req)
    with ledger._connect() as conn:
        row = conn.execute(
            "SELECT record_json FROM cursor_sdk_dispatches WHERE dispatch_id=?",
            (req.dispatch_id,),
        ).fetchone()
    stale_json = row["record_json"]
    patch = {
        "last_error": "x",
        "reason": "stop_not_claimed",
        "status_code": 409,
    }
    with ledger._connect() as conn:
        merge_hop_admit_error_into_record_json_conn(
            conn, dispatch_id=req.dispatch_id, patch=patch
        )
        merge_hop_admit_error_into_record_json_conn(
            conn, dispatch_id=req.dispatch_id, patch=patch
        )
    with ledger._connect() as conn:
        fresh = conn.execute(
            "SELECT record_json FROM cursor_sdk_dispatches WHERE dispatch_id=?",
            (req.dispatch_id,),
        ).fetchone()
    err = json.loads(fresh["record_json"])["hop_admit_error"]
    assert err["attempts"] == 2
    assert err["stop_not_claimed_attempts"] == 2

    bad = merge_hop_patch(stale_json, {"hop_admit_error": patch})
    bad_err = json.loads(bad)["hop_admit_error"]
    assert bad_err["attempts"] == 1
    assert bad_err.get("stop_not_claimed_attempts", 0) <= 1


def _thread_turns(turns: list[dict]) -> patch:
    return patch(
        "services.git_integration_worker.cursor_sdk_closeout.conductor_park_harvest._fetch_thread_turns",
        side_effect=lambda _tid: list(turns),
    )


def _no_harvest_started_at() -> patch:
    return patch(
        "services.git_integration_worker.cursor_sdk_closeout.conductor_hop._harvest_execution_started_at_iso",
        return_value=None,
    )


def _set_row_started_at(ledger: CursorDispatchLedger, dispatch_id: str, iso: str) -> None:
    with ledger._connect() as conn:
        conn.execute(
            "UPDATE cursor_sdk_dispatches SET started_at=? WHERE dispatch_id=?",
            (iso, dispatch_id),
        )


def test_reply_matches_harvest_execution_accepts_from_bus_key() -> None:
    """Agent-bus ``/turns`` exposes author on ``from``, not ``from_agent``."""
    ledger = CursorDispatchLedger.instance()
    req = _req(dispatch_id="park-from-key")
    row = _production_parked_row(ledger, req, closeout_turn=48)
    _set_row_started_at(ledger, req.dispatch_id, "2026-10-13T10:00:00+00:00")
    with ledger._connect() as conn:
        row = conn.execute(
            "SELECT * FROM cursor_sdk_dispatches WHERE dispatch_id=?",
            (req.dispatch_id,),
        ).fetchone()
    mapped = {k: row[k] for k in row.keys()}
    turns = [
        {
            "turn_number": 50,
            "from": "web-anthropic",
            "created_at": "2026-10-13T10:01:00+00:00",
            "subject": f"cdp reply — {_HARVEST_REPLY_UUID}",
        },
    ]
    with _thread_turns(turns), _no_harvest_started_at():
        assert _reply_matches_harvest_execution(mapped, _HARVEST_REPLY_UUID)


def test_reply_matches_harvest_execution_accepts_from_agent_key() -> None:
    ledger = CursorDispatchLedger.instance()
    req = _req(dispatch_id="park-from-agent-key")
    row = _production_parked_row(ledger, req, closeout_turn=48)
    _set_row_started_at(ledger, req.dispatch_id, "2026-10-13T10:00:00+00:00")
    with ledger._connect() as conn:
        row = conn.execute(
            "SELECT * FROM cursor_sdk_dispatches WHERE dispatch_id=?",
            (req.dispatch_id,),
        ).fetchone()
    mapped = {k: row[k] for k in row.keys()}
    turns = [
        {
            "turn_number": 50,
            "from_agent": "web-anthropic",
            "created_at": "2026-10-13T10:01:00+00:00",
            "subject": f"cdp reply — {_HARVEST_REPLY_UUID}",
        },
    ]
    with _thread_turns(turns), _no_harvest_started_at():
        assert _reply_matches_harvest_execution(mapped, _HARVEST_REPLY_UUID)


def _row_without_harvest_token(
    ledger: CursorDispatchLedger, req: CursorDispatchRequest
) -> dict:
    _production_parked_row(ledger, req)
    ledger.merge_record_json(
        dispatch_id=req.dispatch_id,
        patch={"closeout_body": "stop: PARKED_TRANSPORT\nprose only\n"},
    )
    return _reload_row(ledger, req.dispatch_id)


def test_park_harvest_continue_emits_no_harvest_token_refusal() -> None:
    ledger = CursorDispatchLedger.instance()
    req = _req(dispatch_id="park-no-token")
    row = _row_without_harvest_token(ledger, req)
    with patch(
        "services.git_integration_worker.cursor_sdk_closeout.conductor_park_harvest."
        "emit_frontier_sdk_conductor_hop_park_harvest_continue_refused"
    ) as emit_refused:
        assert not park_harvest_continue_owed(row, reply_fn=lambda *_a, **_k: True)
    emit_refused.assert_called_once()
    assert emit_refused.call_args.kwargs["predicate"] == "no_harvest_token"


def test_park_harvest_refusal_emits_once_per_predicate() -> None:
    ledger = CursorDispatchLedger.instance()
    req = _req(dispatch_id="park-refusal-cardinality")
    row = _row_without_harvest_token(ledger, req)
    emit_path = (
        "services.git_integration_worker.cursor_sdk_closeout.conductor_park_harvest."
        "emit_frontier_sdk_conductor_hop_park_harvest_continue_refused"
    )
    with patch(emit_path) as emit_refused:
        assert not park_harvest_continue_owed(row, reply_fn=lambda *_a, **_k: True)
        emit_refused.assert_called_once()
    row2 = _reload_row(ledger, req.dispatch_id)
    with patch(emit_path) as emit_refused:
        assert not park_harvest_continue_owed(row2, reply_fn=lambda *_a, **_k: True)
        emit_refused.assert_not_called()


def test_park_harvest_refusal_not_emitted_before_wake_fired() -> None:
    ledger = CursorDispatchLedger.instance()
    req = _req(dispatch_id="park-pre-wake")
    _production_parked_row(ledger, req, fired=False)
    ledger.merge_record_json(
        dispatch_id=req.dispatch_id,
        patch={"closeout_body": "stop: PARKED_TRANSPORT\nprose only\n"},
    )
    row = _reload_row(ledger, req.dispatch_id)
    with patch(
        "services.git_integration_worker.cursor_sdk_closeout.conductor_park_harvest."
        "emit_frontier_sdk_conductor_hop_park_harvest_continue_refused"
    ) as emit_refused:
        assert not park_harvest_continue_owed(row, reply_fn=lambda *_a, **_k: True)
        emit_refused.assert_not_called()


def test_park_harvest_refusal_not_emitted_when_already_continued() -> None:
    ledger = CursorDispatchLedger.instance()
    req = _req(dispatch_id="park-continued-stamp")
    row = _production_parked_row(ledger, req)
    record = json.loads(row["record_json"])
    record["hop_park_harvest_continued_at"] = time.time()
    row["record_json"] = json.dumps(record)
    with patch(
        "services.git_integration_worker.cursor_sdk_closeout.conductor_park_harvest."
        "emit_frontier_sdk_conductor_hop_park_harvest_continue_refused"
    ) as emit_refused:
        assert not park_harvest_continue_owed(row, reply_fn=lambda *_a, **_k: True)
        emit_refused.assert_not_called()


def test_evaluate_harvest_reply_reply_absent() -> None:
    ledger = CursorDispatchLedger.instance()
    req = _req(dispatch_id="park-reply-absent")
    _production_parked_row(ledger, req, closeout_turn=48)
    _set_row_started_at(ledger, req.dispatch_id, "2026-10-13T10:00:00+00:00")
    mapped = _reload_row(ledger, req.dispatch_id)
    with _thread_turns([]), _no_harvest_started_at():
        matched, predicate = _evaluate_harvest_reply_execution(
            mapped, _HARVEST_REPLY_UUID
        )
    assert not matched
    assert predicate == "reply_absent"


def test_evaluate_harvest_reply_author_mismatch_from_key() -> None:
    ledger = CursorDispatchLedger.instance()
    req = _req(dispatch_id="park-author-mismatch")
    _production_parked_row(ledger, req, closeout_turn=48)
    _set_row_started_at(ledger, req.dispatch_id, "2026-10-13T10:00:00+00:00")
    mapped = _reload_row(ledger, req.dispatch_id)
    turns = [
        {
            "turn_number": 50,
            "from": "cursor",
            "created_at": "2026-10-13T10:01:00+00:00",
            "subject": f"cdp reply — {_HARVEST_REPLY_UUID}",
        },
    ]
    with _thread_turns(turns), _no_harvest_started_at():
        matched, predicate = _evaluate_harvest_reply_execution(
            mapped, _HARVEST_REPLY_UUID
        )
    assert not matched
    assert predicate == "author_mismatch"


def test_evaluate_harvest_reply_echo_then_genuine_web_reply() -> None:
    """39237: non-web echo must not block a later genuine web-anthropic reply."""
    ledger = CursorDispatchLedger.instance()
    req = _req(dispatch_id="park-echo-then-genuine")
    _production_parked_row(ledger, req, closeout_turn=48)
    _set_row_started_at(ledger, req.dispatch_id, "2026-10-13T10:00:00+00:00")
    mapped = _reload_row(ledger, req.dispatch_id)
    short = _HARVEST_REPLY_UUID[:8]
    worker_turns = [
        {
            "turn_number": 30,
            "from": "cursor",
            "created_at": "2026-10-13T10:00:30+00:00",
            "subject": f"cdp reply — {short}",
        },
    ]
    summoning_turns = [
        {
            "turn_number": 80,
            "from": "web-anthropic",
            "created_at": "2026-10-13T10:05:00+00:00",
            "subject": f"cdp reply — {_HARVEST_REPLY_UUID}",
        },
    ]

    def _turns_for_thread(tid: str) -> list[dict]:
        if tid == "9638":
            return summoning_turns
        return worker_turns

    with patch(
        "services.git_integration_worker.cursor_sdk_closeout.conductor_park_harvest."
        "_fetch_thread_turns",
        side_effect=lambda tid: _turns_for_thread(tid),
    ), _no_harvest_started_at():
        matched, predicate = _evaluate_harvest_reply_execution(
            mapped, _HARVEST_REPLY_UUID
        )
    assert matched
    assert predicate is None


def test_evaluate_harvest_reply_at_most_two_thread_fetches() -> None:
    """39237: watermark from fetched turns — one GET per thread per evaluation."""
    ledger = CursorDispatchLedger.instance()
    req = _req(dispatch_id="park-fetch-budget")
    _production_parked_row(ledger, req, closeout_turn=48)
    _set_row_started_at(ledger, req.dispatch_id, "2026-10-13T10:00:00+00:00")
    mapped = _reload_row(ledger, req.dispatch_id)
    fetch = patch(
        "services.git_integration_worker.cursor_sdk_closeout.conductor_park_harvest."
        "_fetch_thread_turns",
        side_effect=lambda _tid: [],
    )
    with fetch as mocked, _no_harvest_started_at():
        _evaluate_harvest_reply_execution(mapped, _HARVEST_REPLY_UUID)
    assert mocked.call_count == 2


def test_evaluate_harvest_reply_reply_failed_for_cdp_failed_subject() -> None:
    ledger = CursorDispatchLedger.instance()
    req = _req(dispatch_id="park-reply-failed")
    _production_parked_row(ledger, req, closeout_turn=48)
    _set_row_started_at(ledger, req.dispatch_id, "2026-10-13T10:00:00+00:00")
    mapped = _reload_row(ledger, req.dispatch_id)
    short = _HARVEST_REPLY_UUID[:8]
    turns = [
        {
            "turn_number": 50,
            "from": "web-anthropic",
            "created_at": "2026-10-13T10:01:00+00:00",
            "subject": f"cdp FAILED — {short}",
        },
    ]
    with _thread_turns(turns), _no_harvest_started_at():
        matched, predicate = _evaluate_harvest_reply_execution(
            mapped, _HARVEST_REPLY_UUID
        )
    assert not matched
    assert predicate == "reply_failed"


def test_evaluate_harvest_reply_started_at_floor() -> None:
    ledger = CursorDispatchLedger.instance()
    req = _req(dispatch_id="park-started-floor")
    _production_parked_row(ledger, req, closeout_turn=48)
    _set_row_started_at(ledger, req.dispatch_id, "2026-10-13T10:05:00+00:00")
    mapped = _reload_row(ledger, req.dispatch_id)
    turns = [
        {
            "turn_number": 50,
            "from": "web-anthropic",
            "created_at": "2026-10-13T10:01:00+00:00",
            "subject": f"cdp reply — {_HARVEST_REPLY_UUID}",
        },
    ]
    with _thread_turns(turns), _no_harvest_started_at():
        matched, predicate = _evaluate_harvest_reply_execution(
            mapped, _HARVEST_REPLY_UUID
        )
    assert not matched
    assert predicate == "started_at_floor"


def test_evaluate_harvest_reply_id_mismatch() -> None:
    ledger = CursorDispatchLedger.instance()
    req = _req(dispatch_id="park-id-mismatch")
    _production_parked_row(ledger, req, closeout_turn=48)
    _set_row_started_at(ledger, req.dispatch_id, "2026-10-13T10:00:00+00:00")
    mapped = _reload_row(ledger, req.dispatch_id)
    other = "f01914a3-015c-4449-be62-1ac2911bf02b"
    turns = [
        {
            "turn_number": 50,
            "from": "web-anthropic",
            "created_at": "2026-10-13T10:01:00+00:00",
            "subject": f"cdp reply — {other}",
        },
    ]
    with _thread_turns(turns), _no_harvest_started_at():
        matched, predicate = _evaluate_harvest_reply_execution(
            mapped, _HARVEST_REPLY_UUID
        )
    assert not matched
    assert predicate == "reply_id_mismatch"


_HARVEST_124_INLINE = "fa67d1a2-ea32-4262-b258-287834b4bb51"
_HARVEST_LINE_ANCHORED = "994dc2b4-d1dd-4206-bd97-2ad0109478bc"


def test_park_harvest_target_token_midline_next_admit_124_class() -> None:
    """16671#124: inline NEXT_ADMIT harvest id when no line-anchored payload."""
    guard = (
        "status: complete\n"
        f"prose continues NEXT_ADMIT: harvest {_HARVEST_124_INLINE} on same line\n"
    )
    assert _park_harvest_target_token(guard) == _HARVEST_124_INLINE


def test_park_harvest_target_token_line_anchored_beats_older_inline() -> None:
    """Line-anchored last NEXT_ADMIT wins over an earlier inline mention."""
    older = "f01914a3-015c-4449-be62-1ac2911bf02b"
    guard = (
        f"quoted earlier NEXT_ADMIT: harvest {older} in prose\n"
        f"NEXT_ADMIT: harvest {_HARVEST_LINE_ANCHORED}\n"
    )
    assert _park_harvest_target_token(guard) == _HARVEST_LINE_ANCHORED


def test_park_harvest_target_token_line_anchored_g1_ignores_inline() -> None:
    guard = (
        "stop: PARKED_TRANSPORT\n"
        "NEXT_ADMIT: harvest G1\n"
        f"prose cites NEXT_ADMIT: harvest {_HARVEST_124_INLINE} inline\n"
    )
    assert _park_harvest_target_token(guard) is None


def test_park_harvest_target_token_two_inline_ids_same_line() -> None:
    a = "f01914a3-015c-4449-be62-1ac2911bf02b"
    b = "642fe99c-45e4-487d-9abb-132c1023a679"
    guard = f"notes NEXT_ADMIT: harvest {a} and NEXT_ADMIT: harvest {b} together\n"
    assert _park_harvest_target_token(guard) is None


def test_park_harvest_target_token_fenced_only_no_line_admit() -> None:
    guard = (
        "```\n"
        f"NEXT_ADMIT: harvest {_HARVEST_124_INLINE}\n"
        "```\n"
    )
    assert _park_harvest_target_token(guard) is None


def test_park_harvest_target_token_rejects_hex_suffix_extension() -> None:
    base = _HARVEST_LINE_ANCHORED[:8]
    guard = f"NEXT_ADMIT: harvest {base}abcd_retry\n"
    assert _park_harvest_target_token(guard) is None


def test_park_harvest_continue_uses_midline_harvest_token() -> None:
    """Continue path resolves inline harvest token (not no_harvest_token refusal)."""
    ledger = CursorDispatchLedger.instance()
    req = _req(dispatch_id="park-midline-124")
    _production_parked_row(ledger, req)
    ledger.merge_record_json(
        dispatch_id=req.dispatch_id,
        patch={
            "closeout_body": (
                "stop: PARKED_TRANSPORT\n"
                f"done with leg NEXT_ADMIT: harvest {_HARVEST_124_INLINE}\n"
            ),
        },
    )
    row = _reload_row(ledger, req.dispatch_id)
    with patch(
        "services.git_integration_worker.cursor_sdk_closeout.conductor_park_harvest."
        "_evaluate_harvest_reply_execution",
        return_value=(False, "reply_absent"),
    ) as evaluate:
        with patch(
            "services.git_integration_worker.cursor_sdk_closeout.conductor_park_harvest."
            "emit_frontier_sdk_conductor_hop_park_harvest_continue_refused"
        ) as emit_refused:
            assert not park_harvest_continue_owed(row, reply_fn=lambda *_a, **_k: True)
        emit_refused.assert_called_once()
        assert emit_refused.call_args.kwargs["predicate"] == "reply_absent"
        assert emit_refused.call_args.kwargs["harvest_token"] == _HARVEST_124_INLINE
    evaluate.assert_called_once()
    assert evaluate.call_args[0][1] == _HARVEST_124_INLINE


@pytest.mark.asyncio
async def test_consult_pending_identical_signatures_park() -> None:
    """Three identical CONSULT_PENDING signatures park once via park_conductor.

    This is the loop park, not the inspection-deferred admit path. The
    inspection pager must stay idle; paging happens inside park_conductor.
    """
    from services.git_integration_worker.cursor_sdk_closeout.conductor_hop_budget import (
        _PARK_REASON_NO_PROGRESS_CAP,
    )
    from services.git_integration_worker.cursor_sdk_closeout.conductor_park_harvest import (
        fire_consult_pending_continue,
    )

    stuck = {
        "hop_entry_gate": "G2",
        "hop_witnessed_done": ["G1"],
        "hop_lane_tip": "lane-a",
        "hop_scoreboard_tip": "board-a",
        "hop_next_admit": "harvest G3",
        "summoning_thread_id": "9638",
        "consult_summoning_after_turn": 1,
        "closeout_turn": 2,
    }
    ledger = CursorDispatchLedger.instance()
    prior = "spawn-parent"
    last_id = "cp-3"
    for idx in range(1, 4):
        dispatch_id = f"cp-{idx}"
        req = _req(dispatch_id=dispatch_id)
        _admit_conductor(ledger, req, record_patch={**stuck, "closeout_stop_tokens": ["CONSULT_PENDING"]})
        ledger.merge_record_json(
            dispatch_id=dispatch_id,
            patch={"hop_seq": idx, "hop_from": prior},
        )
        ledger.mark_terminal(dispatch_id=dispatch_id, terminal_status="completed")
        prior = dispatch_id
        last_id = dispatch_id
    row = _reload_row(ledger, last_id)
    with (
        patch(
            "services.git_integration_worker.cursor_sdk_closeout.conductor_park_harvest.reply_arrived_on_thread",
            return_value=True,
        ),
        patch(
            "services.git_integration_worker.cursor_sdk_closeout.conductor_hop_park.park_conductor_hop_mission",
            AsyncMock(),
        ) as park_mock,
        patch(
            "services.git_integration_worker.cursor_sdk_closeout.conductor_hop_park.page_hop_inspection_requested",
            AsyncMock(return_value=True),
        ) as page_mock,
        patch(
            "services.git_integration_worker.cursor_sdk_closeout.conductor_hop.post_conductor_hop_team_dispatch",
            AsyncMock(return_value=(True, {"dispatch_id": "succ-cp"})),
        ) as post_mock,
    ):
        assert await fire_consult_pending_continue(row) is False
    park_mock.assert_awaited_once()
    assert park_mock.await_args.kwargs["reason"] == _PARK_REASON_NO_PROGRESS_CAP
    page_mock.assert_not_awaited()
    post_mock.assert_not_awaited()


def test_parked_transport_nest_child_not_refused_no_harvest_token() -> None:
    ledger = CursorDispatchLedger.instance()
    req = _req(dispatch_id="park-nest-no-token")
    _admit_conductor(ledger, req)
    ledger.merge_record_json(
        dispatch_id=req.dispatch_id,
        patch={
            "closeout_stop_tokens": ["PARKED_TRANSPORT"],
            "closeout_body": "status: partial\nstop: PARKED_TRANSPORT\n",
            "closeout_turn": 4,
            "closeout_harvest_owed": True,
            "hop_park_harvest_fired_at": 1.0,
        },
    )
    ledger.mark_terminal(dispatch_id=req.dispatch_id, terminal_status="completed")
    row = _reload_row(ledger, req.dispatch_id)
    with (
        patch(
            "services.git_integration_worker.cursor_sdk_closeout.conductor_hop."
            "_terminal_nested_producers_only",
            return_value=True,
        ),
        patch(
            "services.git_integration_worker.cursor_sdk_closeout.conductor_park_harvest."
            "emit_frontier_sdk_conductor_hop_park_harvest_continue_refused"
        ) as emit_refused,
    ):
        assert not park_harvest_continue_owed(row)
    emit_refused.assert_not_called()


def test_continue_refusal_stamps_predicate() -> None:
    ledger = CursorDispatchLedger.instance()
    req = _req(dispatch_id="park-refusal-stamp")
    _admit_conductor(ledger, req)
    ledger.merge_record_json(
        dispatch_id=req.dispatch_id,
        patch={
            "closeout_stop_tokens": ["PARKED_TRANSPORT"],
            "closeout_body": "stop: PARKED_TRANSPORT\nNEXT_ADMIT: harvest none\n",
            "closeout_turn": 4,
            "closeout_harvest_owed": True,
            "hop_park_harvest_fired_at": 1.0,
        },
    )
    ledger.mark_terminal(dispatch_id=req.dispatch_id, terminal_status="completed")
    row = _reload_row(ledger, req.dispatch_id)
    with patch(
        "services.git_integration_worker.cursor_sdk_closeout.conductor_park_harvest."
        "emit_frontier_sdk_conductor_hop_park_harvest_continue_refused"
    ) as emit_refused:
        assert not park_harvest_continue_owed(row)
    emit_refused.assert_called_once()
    assert emit_refused.call_args.kwargs["predicate"] == "no_harvest_token"
