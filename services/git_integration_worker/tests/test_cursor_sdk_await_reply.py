"""Auto-resume of a cursor-sdk dispatch when its outstanding CDP replies land.

Encodes done items 1–5 of friction:34156 (agent-bus:12286): a non-conductor
dispatch that terminalizes with outstanding CDP generates is parked durably
(``park_kind=await_cdp_reply``), the periodic reactor admits exactly one
``resume_of`` child once every awaited reply has landed / failed / timed out,
conductor rows keep the R1 park-harvest path, and hand resumes race cleanly.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from services.git_integration_worker.admission import WorkAdmissionController
from services.git_integration_worker.config import load_config
from services.git_integration_worker.cursor_dispatch_ledger import (
    CursorDispatchLedger,
)
from services.git_integration_worker.cursor_sdk_await_reply import (
    ADMITTED_VIA_AWAIT_RESUME,
    AWAIT_REPLY_FLAG_ENV,
    AWAIT_REPLY_TTL_ENV,
    PARK_KIND_AWAIT_REPLY,
    AwaitedGenerate,
    build_await_resume_request,
    find_reply,
    fired_generates,
    maybe_await_park_at_terminal,
    open_await_rows,
    resume_await_parked_dispatches,
)
from services.git_integration_worker.cursor_sdk_await_reply_gate import (
    ResumeAlreadyAdmitted,
)
from services.git_integration_worker.cursor_sdk_park_ledger import (
    load_park_row,
    open_park_rows,
)
from services.git_integration_worker.models.cursor_api import (
    CursorDispatchRequest,
    CursorDispatchResponse,
)

_WORK_KEY = "friction:34156"
_EXEC = "b08ecb6d-4c1e-4b8e-9d4e-000000000001"
_EXEC2 = "c19fdc7e-5d2f-4c9f-8e5f-000000000002"
_WORKER_THREAD = "14693"
_COORD_THREAD = "14692"
_VERDICT = "VERDICT: APPROVE WITH CHANGES — item 3 is the wrong next change."


@pytest.fixture(autouse=True)
def _isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("CURSOR_DISPATCH_HOME_ROOT", str(tmp_path / "homes"))
    monkeypatch.setenv(AWAIT_REPLY_FLAG_ENV, "1")
    monkeypatch.delenv(AWAIT_REPLY_TTL_ENV, raising=False)
    CursorDispatchLedger._instance = None
    yield
    CursorDispatchLedger._instance = None


@pytest.fixture(autouse=True)
def _admit_stubs(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    """Admission must not need live Cursor auth, MCP parity, or a real bridge.

    Full ``admit_cursor_dispatch`` now refuses explicit lane=A and requires a
    materialized Lane-B worktree — out of scope for this unit suite. The
    reactor under test only needs ledger insert + 200 so ``mark_park_resumed``
    runs; hand-resume 409 still exercises the real route below.
    """
    from fastapi.responses import JSONResponse

    from services.git_integration_worker.cursor_sdk_context import (
        CursorApiKeyResolution,
    )
    from services.git_integration_worker.routes import cursor_sdk as route_mod

    monkeypatch.setattr(
        route_mod,
        "validate_dispatch_context",
        lambda *_a, **_k: {"setting_sources": ["user"], "mcp_server": "vortex"},
    )
    monkeypatch.setattr(
        route_mod,
        "resolve_cursor_api_key",
        lambda *_a, **_k: CursorApiKeyResolution(provenance="env:CURSOR_API_KEY"),
    )
    monkeypatch.setattr(
        route_mod, "capture_wt_baseline_with_hashes", lambda *_a, **_k: {"files": {}}
    )
    spawned = MagicMock(return_value=MagicMock(done=lambda: False))
    monkeypatch.setattr(WorkAdmissionController, "create_tracked_task", spawned)
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_sdk_worktree.pin_lane_worktree_on_admit",
        lambda **_k: None,
    )
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_sdk_concurrency_posture."
        "b_worktree_materialized",
        lambda **_k: True,
    )

    real_admit = route_mod.admit_cursor_dispatch

    async def _ledger_admit(req, *, cfg, controller, request=None):  # noqa: ANN001
        if req.admitted_via != ADMITTED_VIA_AWAIT_RESUME:
            return await real_admit(req, cfg=cfg, controller=controller, request=request)
        ledger = CursorDispatchLedger.instance()
        try:
            ledger.admit(
                req=req,
                fingerprint=ledger.fingerprint(req),
                execution_id=req.execution_id,
                caller_agent=req.caller_agent or "cursor",
                resolved_model=str(req.model).split("/", 1)[-1],
                admission=CursorDispatchResponse(
                    admitted=True,
                    dispatch_id=req.dispatch_id,
                    thread_id=req.thread_id,
                    model_id="m",
                ),
                contract=req.handoff_contract,
                source_repo=str(cfg.source_repo),
                lease_key=str(cfg.source_repo),
                work_key=req.work_key,
                source_ref=req.source_ref or req.work_key,
                identity_class="declared" if req.work_key else None,
            )
        except ResumeAlreadyAdmitted:
            raise
        except Exception as exc:  # noqa: BLE001
            return JSONResponse(
                status_code=409,
                content={"code": type(exc).__name__, "message": str(exc)},
            )
        ledger.mark_running(dispatch_id=req.dispatch_id)
        controller.create_tracked_task(MagicMock())
        return JSONResponse(status_code=200, content={"admitted": True})

    monkeypatch.setattr(route_mod, "admit_cursor_dispatch", _ledger_admit)
    return spawned


@pytest.fixture
def events(monkeypatch: pytest.MonkeyPatch) -> list[Any]:
    log: list[Any] = []
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_sdk_await_reply_events.emit_frontier_event",
        lambda ev: log.append(ev),
    )
    return log


def _bus() -> AsyncMock:
    bus = AsyncMock()
    bus.reply = AsyncMock(
        return_value=MagicMock(status_code=201, body={"turn_number": 5})
    )
    bus.terminate_dispatch = AsyncMock(return_value=MagicMock(status_code=200, body={}))
    return bus


def _controller() -> WorkAdmissionController:
    return WorkAdmissionController(
        ledger=CursorDispatchLedger.instance(),
        worker_id="w",
        pid=0,
        worker_started_at=datetime.now(UTC).isoformat(),
    )


def _seed_running(
    dispatch_id: str,
    *,
    tmp_path: Path,
    thread_id: str = _WORKER_THREAD,
    conductor: bool = False,
    work_key: str | None = _WORK_KEY,
    model_knobs: dict[str, str] | None = None,
) -> None:
    """A live freeform/implement row with SDK identity and an on-disk store."""
    ledger = CursorDispatchLedger.instance()
    contract = "conductor" if conductor else "freeform"
    req = CursorDispatchRequest(
        thread_id=thread_id,
        model="cursor/grok-4.7",
        dispatch_id=dispatch_id,
        execution_id=f"exec-{dispatch_id}",
        caller_agent="cursor",
        message=f"packet body {dispatch_id}",
        handoff_contract=contract,
        prompt_preamble="ORIGINAL PREAMBLE",
        skills=["reasoning-posture"],
        model_knobs=model_knobs or {"effort": "high"},
        lane="B",
        worktree_isolated=True,
        worktree_path=str(tmp_path / f"wt-{dispatch_id}"),
    )
    ledger.admit(
        req=req,
        fingerprint=ledger.fingerprint(req),
        execution_id=req.execution_id,
        caller_agent="cursor",
        resolved_model="grok-4.7",
        admission=CursorDispatchResponse(
            admitted=True, dispatch_id=dispatch_id, thread_id=thread_id, model_id="m"
        ),
        contract=contract,
        source_repo=str(load_config().source_repo),
        lease_key=f"lease-{dispatch_id}",
        work_key=work_key,
        source_ref=work_key,
        identity_class="declared" if work_key else None,
        packet_kind="conductor" if conductor else None,
    )
    ledger.mark_running(dispatch_id=dispatch_id)
    store = tmp_path / f"store-{dispatch_id}"
    store.mkdir(parents=True, exist_ok=True)
    (store / "index.db").write_text("x")
    ledger.record_state_root(dispatch_id=dispatch_id, state_root=str(store))
    ledger.record_sdk_identity(
        dispatch_id=dispatch_id, agent_id=f"agent-{dispatch_id}", run_id="r"
    )


def _spool(tmp_path: Path) -> Path:
    spool = tmp_path / "steer-spool"
    spool.mkdir(parents=True, exist_ok=True)
    return spool


def _record_generate(
    tmp_path: Path,
    dispatch_id: str,
    *,
    execution_id: str = _EXEC,
    thread_id: str = _COORD_THREAD,
    after_turn: int = 3,
) -> None:
    """What the MCP bridge appends when the dispatch's team_dispatch admits a CDP generate."""
    row = {
        "execution_id": execution_id,
        "thread_id": thread_id,
        "after_turn": after_turn,
        "from_agent": "web-anthropic",
        "model": "cdp/opus-5.5",
        "fired_at": datetime.now(UTC).isoformat(),
    }
    path = _spool(tmp_path) / f"{dispatch_id}.cdp-generates.jsonl"
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row) + "\n")


def _record_received(
    tmp_path: Path,
    dispatch_id: str,
    *,
    execution_id: str = _EXEC,
    tool: str = "wait",
) -> None:
    """What the MCP bridge appends when the agent receives a qualifying reply."""
    row = {
        "kind": "received",
        "execution_id": execution_id,
        "tool": tool,
        "received_at": datetime.now(UTC).isoformat(),
    }
    path = _spool(tmp_path) / f"{dispatch_id}.cdp-generates.jsonl"
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row) + "\n")


def _reply_turn(
    *,
    execution_id: str = _EXEC,
    turn_number: int = 4,
    thread: str = _COORD_THREAD,
    outcome: str = "reply",
    text: str = _VERDICT,
) -> dict[str, Any]:
    subject = {
        "reply": f"cdp reply — {execution_id[:8]}",
        "failed": f"cdp FAILED — {execution_id[:8]}",
        "unverified": f"cdp UNVERIFIED — {execution_id[:8]}",
    }[outcome]
    body = (
        "# CDP generate result (cdp/opus-5.5)\n\n"
        f"- execution_id: `{execution_id}`\n"
        "- archive_uri: `cortex://notes/system/threads/14692-cdp-reply.md`\n\n"
        f"{text}"
    )
    return {
        "thread": thread,
        "turn_number": turn_number,
        "from": "web-anthropic",
        "to": "dispatch",
        "subject": subject,
        "body": body,
        "status": "open",
    }


class _Turns:
    """Injectable ``bus_turns_fn(thread_id, after_turn) -> list[turn]``."""

    def __init__(self, turns: dict[str, list[dict[str, Any]]] | None = None) -> None:
        self.turns = turns or {}
        self.calls: list[tuple[str, int]] = []

    def __call__(self, thread_id: str, after_turn: int) -> list[dict[str, Any]]:
        self.calls.append((thread_id, after_turn))
        return [
            t
            for t in self.turns.get(thread_id, [])
            if int(t["turn_number"]) > after_turn
        ]


def _row(dispatch_id: str) -> dict[str, Any] | None:
    with CursorDispatchLedger.instance()._connect() as conn:
        row = conn.execute(
            "SELECT * FROM cursor_sdk_dispatches WHERE dispatch_id=?", (dispatch_id,)
        ).fetchone()
    return {k: row[k] for k in row.keys()} if row is not None else None


def _children(parent_id: str) -> list[str]:
    with CursorDispatchLedger.instance()._connect() as conn:
        rows = conn.execute(
            "SELECT dispatch_id FROM cursor_sdk_dispatches WHERE resume_of=? "
            "ORDER BY rowid",
            (parent_id,),
        ).fetchall()
    return [str(r["dispatch_id"]) for r in rows]


def _mark_completed(dispatch_id: str) -> None:
    """What ``_mark_terminal_and_promote`` does right after the park hook."""
    CursorDispatchLedger.instance().mark_terminal(
        dispatch_id=dispatch_id, terminal_status="completed"
    )


async def _park(
    dispatch_id: str, *, bus: AsyncMock, turns: _Turns, tmp_path: Path
) -> bool:
    return await maybe_await_park_at_terminal(
        dispatch_id=dispatch_id,
        thread_id=_row(dispatch_id)["thread_id"],
        execution_id=f"exec-{dispatch_id}",
        bus=bus,
        spool_dir=_spool(tmp_path),
        bus_turns_fn=turns,
    )


async def _tick(*, bus: AsyncMock, turns: _Turns, now: datetime | None = None):
    return await resume_await_parked_dispatches(
        cfg=load_config(),
        controller=_controller(),
        code_version="abc1234",
        bus=bus,
        bus_turns_fn=turns,
        now=now,
    )


# ------------------------------------------------------------- done item 1


def test_fired_generates_reads_bridge_ledger(tmp_path: Path) -> None:
    _record_generate(tmp_path, "d1")
    _record_generate(tmp_path, "d1", execution_id=_EXEC2, thread_id="14693", after_turn=2)
    (_spool(tmp_path) / "d1.cdp-generates.jsonl").open("a").write("not json\n")
    fired = fired_generates("d1", spool_dir=_spool(tmp_path))
    assert [g.execution_id for g in fired] == [_EXEC, _EXEC2]
    assert fired[0] == AwaitedGenerate(
        execution_id=_EXEC,
        thread_id=_COORD_THREAD,
        after_turn=3,
        from_agent="web-anthropic",
        model="cdp/opus-5.5",
        fired_at=fired[0].fired_at,
    )
    assert fired_generates("nobody", spool_dir=_spool(tmp_path)) == []


@pytest.mark.asyncio
async def test_terminal_with_outstanding_generate_parks_row_durably(
    tmp_path: Path, events: list[Any]
) -> None:
    _seed_running("d-park", tmp_path=tmp_path)
    _record_generate(tmp_path, "d-park")
    bus = _bus()
    turns = _Turns()  # nothing landed yet on the coord thread

    parked = await _park("d-park", bus=bus, turns=turns, tmp_path=tmp_path)

    assert parked is True
    assert turns.calls == [(_COORD_THREAD, 3)]
    row = _row("d-park")
    assert row["park_kind"] == PARK_KIND_AWAIT_REPLY
    assert row["park_resumed_by"] is None
    assert row["parked_at"] and row["park_expires_at"] > row["parked_at"]
    record = json.loads(row["record_json"])
    assert record["resume_retain"] is True
    assert record["park"]["kind"] == PARK_KIND_AWAIT_REPLY
    assert [g["execution_id"] for g in record["park"]["awaited"]] == [_EXEC]
    assert record["park"]["awaited"][0]["thread_id"] == _COORD_THREAD
    # The hook does not pre-empt the ordinary terminal: status is the route's job.
    assert row["status"] == "running"
    _mark_completed("d-park")
    assert _row("d-park")["status"] == "completed"
    # Not plain complete: the AWAITING turn names the execution id the lineage waits on.
    awaiting = [c.kwargs for c in bus.reply.await_args_list]
    assert len(awaiting) == 1
    assert awaiting[0]["thread_id"] == _WORKER_THREAD
    assert "AWAITING CDP REPLY" in awaiting[0]["subject"]
    assert _EXEC in awaiting[0]["body"] and _COORD_THREAD in awaiting[0]["body"]
    assert [ev.signal for ev in events] == ["sdk.await_reply.parked"]
    # Durable: a fresh ledger handle (GIW restart) still sees the open await row.
    CursorDispatchLedger._instance = None
    assert [r.dispatch_id for r in open_await_rows()] == ["d-park"]
    # And the restart-park reactor does not claim it.
    assert open_park_rows() == []


@pytest.mark.asyncio
async def test_reply_received_before_exit_does_not_park(tmp_path: Path) -> None:
    """R1: only a reply the agent received is excluded from outstanding."""
    _seed_running("d-recv", tmp_path=tmp_path)
    _record_generate(tmp_path, "d-recv")
    _record_received(tmp_path, "d-recv")
    turns = _Turns({_COORD_THREAD: [_reply_turn()]})
    bus = _bus()
    assert await _park("d-recv", bus=bus, turns=turns, tmp_path=tmp_path) is False
    assert _row("d-recv")["park_kind"] is None
    bus.reply.assert_not_awaited()


@pytest.mark.asyncio
async def test_reply_landed_unconsumed_parks_and_resumes_at_once(
    tmp_path: Path, _admit_stubs: MagicMock
) -> None:
    """R1: landed before exit but never received ⇒ park + immediate resume."""
    _seed_running("d-uncons", tmp_path=tmp_path)
    _record_generate(tmp_path, "d-uncons")
    # Reply is on the coord thread; no received marker in the bridge ledger.
    turns = _Turns({_COORD_THREAD: [_reply_turn()]})
    bus = _bus()
    assert await _park("d-uncons", bus=bus, turns=turns, tmp_path=tmp_path) is True
    assert _row("d-uncons")["park_kind"] == PARK_KIND_AWAIT_REPLY
    _mark_completed("d-uncons")
    # Closeout's immediate resume pass (same as the post-park tick).
    summary = await _tick(bus=bus, turns=turns)
    assert summary.admitted == [("d-uncons", "d-uncons-c1")]
    preamble = json.loads(_row("d-uncons-c1")["record_json"])["prompt_preamble"]
    assert _VERDICT in preamble
    assert _admit_stubs.call_count == 1


@pytest.mark.asyncio
async def test_reply_landed_after_exit_parks_and_resumes_on_tick(
    tmp_path: Path, _admit_stubs: MagicMock
) -> None:
    """R1: reply arrives after terminal ⇒ park, resume on the reactor tick."""
    _seed_running("d-after", tmp_path=tmp_path)
    _record_generate(tmp_path, "d-after")
    bus = _bus()
    turns = _Turns()
    assert await _park("d-after", bus=bus, turns=turns, tmp_path=tmp_path) is True
    _mark_completed("d-after")
    assert (await _tick(bus=bus, turns=turns)).waiting == ["d-after"]
    turns.turns[_COORD_THREAD] = [_reply_turn()]
    summary = await _tick(bus=bus, turns=turns)
    assert summary.admitted == [("d-after", "d-after-c1")]
    assert _admit_stubs.call_count == 1


@pytest.mark.asyncio
async def test_no_bridge_record_or_flag_off_keeps_todays_behaviour(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed_running("d-plain", tmp_path=tmp_path)
    bus = _bus()
    assert await _park("d-plain", bus=bus, turns=_Turns(), tmp_path=tmp_path) is False
    assert _row("d-plain")["park_kind"] is None
    _mark_completed("d-plain")

    _seed_running(
        "d-flag",
        tmp_path=tmp_path,
        thread_id="14694",
        work_key="friction:34156-b",
    )
    _record_generate(tmp_path, "d-flag")
    monkeypatch.setenv(AWAIT_REPLY_FLAG_ENV, "0")
    assert await _park("d-flag", bus=bus, turns=_Turns(), tmp_path=tmp_path) is False
    assert _row("d-flag")["park_kind"] is None
    bus.reply.assert_not_awaited()


@pytest.mark.asyncio
async def test_bus_snapshot_failure_parks_rather_than_drops(tmp_path: Path) -> None:
    """Bus unreachable at terminal: the generate stays awaited; the tick re-checks."""
    _seed_running("d-busdown", tmp_path=tmp_path)
    _record_generate(tmp_path, "d-busdown")

    def _boom(_thread: str, _after: int) -> list[dict[str, Any]]:
        raise ConnectionError("bus down")

    assert await _park("d-busdown", bus=_bus(), turns=_boom, tmp_path=tmp_path) is True
    assert _row("d-busdown")["park_kind"] == PARK_KIND_AWAIT_REPLY


# ------------------------------------------------------------- done item 2


def test_find_reply_matches_execution_id_from_web_anthropic_only() -> None:
    awaited = AwaitedGenerate(
        execution_id=_EXEC,
        thread_id=_COORD_THREAD,
        after_turn=3,
        from_agent="web-anthropic",
        model="cdp/opus-5.5",
        fired_at="t",
    )
    other_seat = dict(_reply_turn(), **{"from": "cursor-sdk"})
    other_exec = _reply_turn(execution_id=_EXEC2, turn_number=5)
    assert find_reply(awaited, [other_seat, other_exec]) is None
    hit = find_reply(awaited, [other_seat, other_exec, _reply_turn(turn_number=6)])
    assert hit is not None
    assert (hit.turn_number, hit.outcome) == (6, "reply")
    assert _VERDICT in hit.body
    failed = find_reply(awaited, [_reply_turn(outcome="failed", turn_number=7)])
    assert failed is not None and failed.outcome == "failed"


@pytest.mark.asyncio
async def test_reactor_admits_resume_child_when_reply_lands_on_coord_thread(
    tmp_path: Path, events: list[Any], _admit_stubs: MagicMock
) -> None:
    _seed_running("d-res", tmp_path=tmp_path, model_knobs={"effort": "high"})
    _record_generate(tmp_path, "d-res")
    bus = _bus()
    turns = _Turns()
    assert await _park("d-res", bus=bus, turns=turns, tmp_path=tmp_path) is True
    _mark_completed("d-res")

    # Tick before the reply: nothing admitted, row stays open.
    first = await _tick(bus=bus, turns=turns)
    assert first.admitted == [] and first.waiting == ["d-res"]
    assert _children("d-res") == []

    # The reply lands on the coord thread (not the worker thread) — one tick later.
    turns.turns[_COORD_THREAD] = [_reply_turn()]
    second = await _tick(bus=bus, turns=turns)

    assert second.admitted == [("d-res", "d-res-c1")]
    child = _row("d-res-c1")
    assert child is not None
    assert child["resume_of"] == "d-res"
    assert child["thread_id"] == _WORKER_THREAD  # reuse_thread: the worker's own lane
    assert child["execution_id"] == "exec-d-res"
    assert child["work_key"] == _WORK_KEY
    assert child["status"] in ("admitted", "running")
    record = json.loads(child["record_json"])
    assert record["admitted_via"] == ADMITTED_VIA_AWAIT_RESUME
    assert record["model"] == "cursor/grok-4.7"
    assert record["model_knobs"] == {"effort": "high"}
    assert record["lane"] == "B"
    assert record["message"] == "packet body d-res"
    preamble = record["prompt_preamble"]
    assert preamble.startswith("CDP-REPLY-RESUME v1")
    assert "dispatch d-res on agent-bus:14693" in preamble
    assert _EXEC in preamble and _VERDICT in preamble
    assert f"agent-bus:{_COORD_THREAD}#4" in preamble
    assert preamble.endswith("ORIGINAL PREAMBLE")
    assert _row("d-res")["park_resumed_by"] == "d-res-c1"
    assert _admit_stubs.call_count == 1
    resumed = [c.kwargs for c in bus.reply.await_args_list if "RESUMED" in c.kwargs["subject"]]
    assert len(resumed) == 1 and resumed[0]["thread_id"] == _WORKER_THREAD
    assert "d-res-c1" in resumed[0]["subject"] and "resume_of d-res" in resumed[0]["subject"]
    signals = [ev.signal for ev in events]
    assert signals.count("sdk.await_reply.resume_admitted") == 1


def test_resume_request_builder_inherits_identity(tmp_path: Path) -> None:
    import asyncio

    _seed_running("d-build", tmp_path=tmp_path)
    _record_generate(tmp_path, "d-build")
    asyncio.run(_park("d-build", bus=_bus(), turns=_Turns(), tmp_path=tmp_path))
    row = load_park_row(dispatch_id="d-build")
    assert row is not None
    hit = find_reply(fired_generates("d-build", spool_dir=_spool(tmp_path))[0], [_reply_turn()])
    req = build_await_resume_request(row, replies=[hit], attempt=2, code_version="deadbee")
    assert req.dispatch_id == "d-build-c2"
    assert req.resume_of == "d-build"
    assert req.execution_id == "exec-d-build"
    assert req.thread_id == _WORKER_THREAD
    assert req.model == "cursor/grok-4.7"
    assert req.model_knobs == {"effort": "high"}
    assert req.admitted_via == ADMITTED_VIA_AWAIT_RESUME
    assert req.lane == "B" and req.worktree_path == str(tmp_path / "wt-d-build")
    assert req.prompt_preamble is not None and "code_version deadbee" in req.prompt_preamble


# ------------------------------------------------------------- done item 3


@pytest.mark.asyncio
async def test_second_tick_and_second_reply_never_mint_a_second_child(
    tmp_path: Path, _admit_stubs: MagicMock
) -> None:
    _seed_running("d-once", tmp_path=tmp_path)
    _record_generate(tmp_path, "d-once")
    bus = _bus()
    turns = _Turns({_COORD_THREAD: []})
    await _park("d-once", bus=bus, turns=turns, tmp_path=tmp_path)
    _mark_completed("d-once")
    turns.turns[_COORD_THREAD] = [_reply_turn()]
    assert (await _tick(bus=bus, turns=turns)).admitted == [("d-once", "d-once-c1")]

    turns.turns[_COORD_THREAD].append(_reply_turn(turn_number=9, text="second reply"))
    again = await _tick(bus=bus, turns=turns)
    assert again.admitted == [] and again.reconciled == []
    assert _children("d-once") == ["d-once-c1"]
    # Reactor after a GIW restart (fresh ledger handle): still one child.
    CursorDispatchLedger._instance = None
    assert (await _tick(bus=_bus(), turns=turns)).admitted == []
    assert _children("d-once") == ["d-once-c1"]
    assert _admit_stubs.call_count == 1


@pytest.mark.asyncio
async def test_hand_resume_after_auto_child_is_refused_naming_the_child(
    tmp_path: Path,
) -> None:
    _seed_running("d-hand", tmp_path=tmp_path)
    _record_generate(tmp_path, "d-hand")
    bus = _bus()
    turns = _Turns({_COORD_THREAD: [_reply_turn()]})
    await _park("d-hand", bus=bus, turns=_Turns(), tmp_path=tmp_path)
    _mark_completed("d-hand")
    assert (await _tick(bus=bus, turns=turns)).admitted == [("d-hand", "d-hand-c1")]

    ledger = CursorDispatchLedger.instance()
    hand = CursorDispatchRequest(
        thread_id=_WORKER_THREAD,
        model="cursor/grok-4.7",
        dispatch_id="hand-child",
        execution_id="exec-hand-child",
        caller_agent="cursor",
        message="operator resume after the auto child",
        handoff_contract="freeform",
        resume_of="d-hand",
        lane="B",
        worktree_isolated=True,
        worktree_path=str(tmp_path / "wt-hand"),
    )
    with pytest.raises(ResumeAlreadyAdmitted) as excinfo:
        ledger.admit(
            req=hand,
            fingerprint=ledger.fingerprint(hand),
            execution_id=hand.execution_id,
            caller_agent="cursor",
            resolved_model="grok-4.7",
            admission=CursorDispatchResponse(
                admitted=True,
                dispatch_id="hand-child",
                thread_id=_WORKER_THREAD,
                model_id="m",
            ),
            contract="freeform",
            source_repo=str(load_config().source_repo),
            lease_key="lease-hand",
            work_key=_WORK_KEY,
            identity_class="declared",
        )
    assert excinfo.value.parent_dispatch_id == "d-hand"
    assert excinfo.value.existing_child_id == "d-hand-c1"
    assert _row("hand-child") is None
    assert _children("d-hand") == ["d-hand-c1"]

    # Route-level: the refusal is a 409 that names the existing child.
    from services.git_integration_worker.routes import cursor_sdk as route_mod

    response = await route_mod.admit_cursor_dispatch(
        hand, cfg=load_config(), controller=_controller()
    )
    assert response.status_code == 409
    body = json.loads(bytes(response.body).decode())
    assert body.get("code") == "CURSOR_RESUME_ALREADY_ADMITTED"
    assert "d-hand-c1" in json.dumps(body)


@pytest.mark.asyncio
async def test_hand_resume_first_closes_the_park_and_reactor_stands_down(
    tmp_path: Path, _admit_stubs: MagicMock
) -> None:
    _seed_running("d-first", tmp_path=tmp_path)
    _record_generate(tmp_path, "d-first")
    bus = _bus()
    await _park("d-first", bus=bus, turns=_Turns(), tmp_path=tmp_path)
    _mark_completed("d-first")

    ledger = CursorDispatchLedger.instance()
    hand = CursorDispatchRequest(
        thread_id=_WORKER_THREAD,
        model="cursor/grok-4.7",
        dispatch_id="hand-first",
        execution_id="exec-hand-first",
        caller_agent="cursor",
        message="operator resumed by hand before the reply landed",
        handoff_contract="freeform",
        resume_of="d-first",
    )
    assert (
        ledger.admit(
            req=hand,
            fingerprint=ledger.fingerprint(hand),
            execution_id=hand.execution_id,
            caller_agent="cursor",
            resolved_model="grok-4.7",
            admission=CursorDispatchResponse(
                admitted=True,
                dispatch_id="hand-first",
                thread_id=_WORKER_THREAD,
                model_id="m",
            ),
            contract="freeform",
            source_repo=str(load_config().source_repo),
            lease_key="lease-hand-first",
            work_key=_WORK_KEY,
            identity_class="declared",
        )
        is None
    )
    assert _row("d-first")["park_resumed_by"] == "hand-first"

    turns = _Turns({_COORD_THREAD: [_reply_turn()]})
    summary = await _tick(bus=bus, turns=turns)
    assert summary.admitted == []
    assert _children("d-first") == ["hand-first"]
    assert _admit_stubs.call_count == 0


@pytest.mark.asyncio
async def test_crash_between_admit_and_stamp_is_reconciled_not_readmitted(
    tmp_path: Path,
) -> None:
    _seed_running("d-crash", tmp_path=tmp_path)
    _record_generate(tmp_path, "d-crash")
    bus = _bus()
    await _park("d-crash", bus=bus, turns=_Turns(), tmp_path=tmp_path)
    _mark_completed("d-crash")
    turns = _Turns({_COORD_THREAD: [_reply_turn()]})
    assert (await _tick(bus=bus, turns=turns)).admitted == [("d-crash", "d-crash-c1")]
    with CursorDispatchLedger.instance()._connect() as conn:
        conn.execute(
            "UPDATE cursor_sdk_dispatches SET park_resumed_by=NULL WHERE dispatch_id='d-crash'"
        )
    second = await _tick(bus=bus, turns=turns)
    assert second.admitted == [] and second.reconciled == [("d-crash", "d-crash-c1")]
    assert _row("d-crash")["park_resumed_by"] == "d-crash-c1"


# ------------------------------------------------------------- done item 4


@pytest.mark.asyncio
async def test_conductor_row_keeps_r1_path_and_never_gets_a_second_resume(
    tmp_path: Path, _admit_stubs: MagicMock
) -> None:
    _seed_running("d-cond", tmp_path=tmp_path, conductor=True)
    _record_generate(tmp_path, "d-cond")
    bus = _bus()
    assert await _park("d-cond", bus=bus, turns=_Turns(), tmp_path=tmp_path) is False
    row = _row("d-cond")
    assert row["park_kind"] is None and row["parked_at"] is None
    assert "resume_retain" not in json.loads(row["record_json"])
    bus.reply.assert_not_awaited()
    assert open_await_rows() == []

    # Defensive: even a conductor row somebody stamped with the kind is skipped.
    _mark_completed("d-cond")
    with CursorDispatchLedger.instance()._connect() as conn:
        conn.execute(
            "UPDATE cursor_sdk_dispatches SET park_kind=?, parked_at=?, "
            "park_expires_at=? WHERE dispatch_id='d-cond'",
            (
                PARK_KIND_AWAIT_REPLY,
                datetime.now(UTC).isoformat(),
                (datetime.now(UTC) + timedelta(days=1)).isoformat(),
            ),
        )
    summary = await _tick(bus=bus, turns=_Turns({_COORD_THREAD: [_reply_turn()]}))
    assert summary.admitted == [] and summary.skipped_conductor == ["d-cond"]
    assert _children("d-cond") == []
    assert _admit_stubs.call_count == 0


# ------------------------------------------------------------- done item 5


@pytest.mark.asyncio
async def test_failed_generate_resumes_with_failure_text(tmp_path: Path) -> None:
    _seed_running("d-fail", tmp_path=tmp_path)
    _record_generate(tmp_path, "d-fail")
    bus = _bus()
    await _park("d-fail", bus=bus, turns=_Turns(), tmp_path=tmp_path)
    _mark_completed("d-fail")
    turns = _Turns(
        {
            _COORD_THREAD: [
                _reply_turn(
                    outcome="failed",
                    text="CSE stream stopped: WALL_CLOCK_EXCEEDED",
                )
            ]
        }
    )
    summary = await _tick(bus=bus, turns=turns)
    assert summary.admitted == [("d-fail", "d-fail-c1")]
    preamble = json.loads(_row("d-fail-c1")["record_json"])["prompt_preamble"]
    assert "(failed)" in preamble
    assert "WALL_CLOCK_EXCEEDED" in preamble


@pytest.mark.asyncio
async def test_ttl_expiry_resumes_once_with_timeout_text_and_never_hangs(
    tmp_path: Path, events: list[Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(AWAIT_REPLY_TTL_ENV, "600")
    _seed_running("d-ttl", tmp_path=tmp_path)
    _record_generate(tmp_path, "d-ttl")
    bus = _bus()
    turns = _Turns()
    await _park("d-ttl", bus=bus, turns=turns, tmp_path=tmp_path)
    _mark_completed("d-ttl")
    row = _row("d-ttl")
    parked_at = datetime.fromisoformat(row["parked_at"])
    expires_at = datetime.fromisoformat(row["park_expires_at"])
    assert (expires_at - parked_at) == timedelta(seconds=600)

    before = await _tick(bus=bus, turns=turns, now=parked_at + timedelta(seconds=599))
    assert before.admitted == [] and before.waiting == ["d-ttl"]

    after = await _tick(bus=bus, turns=turns, now=parked_at + timedelta(seconds=601))
    assert after.admitted == [("d-ttl", "d-ttl-c1")]
    preamble = json.loads(_row("d-ttl-c1")["record_json"])["prompt_preamble"]
    assert "(timeout)" in preamble and _EXEC in preamble and "600" in preamble
    assert "treat the generate as failed" in preamble

    again = await _tick(bus=bus, turns=turns, now=parked_at + timedelta(seconds=900))
    assert again.admitted == []
    assert _children("d-ttl") == ["d-ttl-c1"]
    assert [ev.signal for ev in events].count("sdk.await_reply.resume_admitted") == 1


@pytest.mark.asyncio
async def test_several_generates_resume_once_after_all_land(
    tmp_path: Path, _admit_stubs: MagicMock
) -> None:
    _seed_running("d-multi", tmp_path=tmp_path)
    _record_generate(tmp_path, "d-multi")
    _record_generate(
        tmp_path, "d-multi", execution_id=_EXEC2, thread_id=_WORKER_THREAD, after_turn=2
    )
    bus = _bus()
    turns = _Turns()
    await _park("d-multi", bus=bus, turns=turns, tmp_path=tmp_path)
    awaited = json.loads(_row("d-multi")["record_json"])["park"]["awaited"]
    assert [g["execution_id"] for g in awaited] == [_EXEC, _EXEC2]
    _mark_completed("d-multi")

    turns.turns[_COORD_THREAD] = [_reply_turn()]
    partial = await _tick(bus=bus, turns=turns)
    assert partial.admitted == [] and partial.waiting == ["d-multi"]

    turns.turns[_WORKER_THREAD] = [
        _reply_turn(execution_id=_EXEC2, thread=_WORKER_THREAD, turn_number=6, text="second verdict")
    ]
    full = await _tick(bus=bus, turns=turns)
    assert full.admitted == [("d-multi", "d-multi-c1")]
    preamble = json.loads(_row("d-multi-c1")["record_json"])["prompt_preamble"]
    assert "Awaited reply 1/2" in preamble and "Awaited reply 2/2" in preamble
    assert _VERDICT in preamble and "second verdict" in preamble
    assert _children("d-multi") == ["d-multi-c1"]
    assert _admit_stubs.call_count == 1


@pytest.mark.asyncio
async def test_permanent_ineligibility_expires_the_park_once(
    tmp_path: Path, events: list[Any]
) -> None:
    """Store dir gone ⇒ no agent to resume: close the link once, post awareness, stop."""
    import shutil

    _seed_running("d-nostore", tmp_path=tmp_path)
    _record_generate(tmp_path, "d-nostore")
    bus = _bus()
    await _park("d-nostore", bus=bus, turns=_Turns(), tmp_path=tmp_path)
    _mark_completed("d-nostore")
    shutil.rmtree(tmp_path / "store-d-nostore")
    turns = _Turns({_COORD_THREAD: [_reply_turn()]})

    first = await _tick(bus=bus, turns=turns)
    assert first.admitted == [] and first.expired == ["d-nostore"]
    assert json.loads(_row("d-nostore")["record_json"])["park"]["expired_at"]
    bus.terminate_dispatch.assert_awaited_once_with(
        thread_id=_WORKER_THREAD, terminal_status="completed", execution_id="exec-d-nostore"
    )
    assert [ev.signal for ev in events].count("sdk.await_reply.expired") == 1

    second = await _tick(bus=bus, turns=turns)
    assert second.expired == [] and second.admitted == []
    assert _children("d-nostore") == []
