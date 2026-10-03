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
from types import SimpleNamespace
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
    announce_await_parked,
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
from services.git_integration_worker.cursor_sdk_orphan import BridgeReapResult
from services.git_integration_worker.cursor_sdk_park_ledger import (
    load_park_row,
    open_park_rows,
)
from services.git_integration_worker.cursor_sdk_worktree_prune import ReapSweepResult
from services.git_integration_worker.models.cursor_api import (
    CursorDispatchRequest,
    CursorDispatchResponse,
)
from services.git_integration_worker.routes import cursor_sdk as route_mod

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
            return await real_admit(
                req, cfg=cfg, controller=controller, request=request
            )
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
    _record_generate(
        tmp_path, "d1", execution_id=_EXEC2, thread_id="14693", after_turn=2
    )
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
    # F5: park no longer snapshots the bus; the reactor observes replies.
    assert turns.calls == []
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
    # Announce runs after the terminal mark (1c).
    bus.reply.assert_not_awaited()
    assert events == []
    _mark_completed("d-park")
    assert _row("d-park")["status"] == "completed"
    await announce_await_parked(dispatch_id="d-park", thread_id=_WORKER_THREAD, bus=bus)
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
    """Bus unreachable at park is irrelevant (F5); tick keeps waiting on bus-down."""
    _seed_running("d-busdown", tmp_path=tmp_path)
    _record_generate(tmp_path, "d-busdown")

    def _boom(_thread: str, _after: int) -> list[dict[str, Any]]:
        raise ConnectionError("bus down")

    assert await _park("d-busdown", bus=_bus(), turns=_boom, tmp_path=tmp_path) is True
    assert _row("d-busdown")["park_kind"] == PARK_KIND_AWAIT_REPLY
    _mark_completed("d-busdown")
    summary = await _tick(bus=_bus(), turns=_boom)
    assert summary.waiting == ["d-busdown"]
    assert summary.admitted == []


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
    resumed = [
        c.kwargs for c in bus.reply.await_args_list if "RESUMED" in c.kwargs["subject"]
    ]
    assert len(resumed) == 1 and resumed[0]["thread_id"] == _WORKER_THREAD
    assert (
        "d-res-c1" in resumed[0]["subject"]
        and "resume_of d-res" in resumed[0]["subject"]
    )
    signals = [ev.signal for ev in events]
    assert signals.count("sdk.await_reply.resume_admitted") == 1


def test_resume_request_builder_inherits_identity(tmp_path: Path) -> None:
    import asyncio

    _seed_running("d-build", tmp_path=tmp_path)
    _record_generate(tmp_path, "d-build")
    asyncio.run(_park("d-build", bus=_bus(), turns=_Turns(), tmp_path=tmp_path))
    row = load_park_row(dispatch_id="d-build")
    assert row is not None
    hit = find_reply(
        fired_generates("d-build", spool_dir=_spool(tmp_path))[0], [_reply_turn()]
    )
    req = build_await_resume_request(
        row, replies=[hit], attempt=2, code_version="deadbee"
    )
    assert req.dispatch_id == "d-build-c2"
    assert req.resume_of == "d-build"
    assert req.execution_id == "exec-d-build"
    assert req.thread_id == _WORKER_THREAD
    assert req.model == "cursor/grok-4.7"
    assert req.model_knobs == {"effort": "high"}
    assert req.admitted_via == ADMITTED_VIA_AWAIT_RESUME
    assert req.lane == "B" and req.worktree_path == str(tmp_path / "wt-d-build")
    assert (
        req.prompt_preamble is not None
        and "code_version deadbee" in req.prompt_preamble
    )


def _await_park_row(
    *,
    dispatch_id: str,
    record: dict[str, Any],
    source_repo: str | None = None,
) -> Any:
    from services.git_integration_worker.cursor_sdk_park_ledger import ParkRow

    return ParkRow(
        dispatch_id=dispatch_id,
        thread_id=_WORKER_THREAD,
        execution_id=f"exec-{dispatch_id}",
        caller_agent="cursor",
        resolved_model="cursor/grok-4.7",
        status="completed",
        terminal_status="completed",
        sdk_agent_id="agent-x",
        state_root=None,
        source_ref=_WORK_KEY,
        work_key=_WORK_KEY,
        contract="freeform",
        packet_path=None,
        park_kind=PARK_KIND_AWAIT_REPLY,
        park_intent_id=None,
        parked_at="2026-10-03T00:00:00+00:00",
        park_resumed_by=None,
        park_expires_at=None,
        record_json=json.dumps(record),
        source_repo=source_repo,
    )


def test_await_resume_builder_copies_record_workspace() -> None:
    row = _await_park_row(
        dispatch_id="d-ws",
        record={
            "workspace": "cryptax",
            "model": "cursor/grok-4.7",
            "message": "continue",
            "handoff_contract": "freeform",
            "park": {},
        },
        source_repo="/mnt/torus/projects/universal-llm-gateway",
    )
    req = build_await_resume_request(row, replies=[], attempt=1, code_version="v")
    assert req.workspace == "cryptax"


def test_await_resume_builder_derives_workspace_from_source_repo() -> None:
    row = _await_park_row(
        dispatch_id="d-legacy-ws",
        record={"model": "cursor/grok-4.7", "message": "continue", "park": {}},
        source_repo="/mnt/torus/projects/cryptax",
    )
    req = build_await_resume_request(row, replies=[], attempt=1, code_version="v")
    assert req.workspace == "cryptax"


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
        _reply_turn(
            execution_id=_EXEC2,
            thread=_WORKER_THREAD,
            turn_number=6,
            text="second verdict",
        )
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
        thread_id=_WORKER_THREAD,
        terminal_status="completed",
        execution_id="exec-d-nostore",
    )
    assert [ev.signal for ev in events].count("sdk.await_reply.expired") == 1

    second = await _tick(bus=bus, turns=turns)
    assert second.expired == [] and second.admitted == []
    assert _children("d-nostore") == []


# ------------------------------------------------------------- review F1–F9 + gaps


@pytest.mark.asyncio
async def test_f1_park_hook_exception_still_allows_terminal_mark(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """F1: mark_await_parked raising must not leave the closeout without a terminal."""
    from services.git_integration_worker import cursor_sdk_await_reply as mod

    _seed_running("d-f1", tmp_path=tmp_path)
    _record_generate(tmp_path, "d-f1")

    def _boom(**_kw: Any) -> None:
        raise RuntimeError("spool torn")

    monkeypatch.setattr(mod, "mark_await_parked", _boom)
    bus = _bus()
    assert await _park("d-f1", bus=bus, turns=_Turns(), tmp_path=tmp_path) is False
    _mark_completed("d-f1")
    assert _row("d-f1")["status"] == "completed"
    assert _row("d-f1")["park_kind"] is None


@pytest.mark.asyncio
async def test_f1_awaiting_post_failure_still_counts_as_parked(
    tmp_path: Path,
) -> None:
    """F1/1c: park stamp succeeds even when the post-terminal announce fails."""
    _seed_running("d-f1post", tmp_path=tmp_path)
    _record_generate(tmp_path, "d-f1post")
    bus = _bus()
    parked = await _park("d-f1post", bus=bus, turns=_Turns(), tmp_path=tmp_path)
    assert parked is True
    assert _row("d-f1post")["park_kind"] == PARK_KIND_AWAIT_REPLY
    bus.reply = AsyncMock(side_effect=RuntimeError("bus reply down"))
    await announce_await_parked(
        dispatch_id="d-f1post", thread_id=_WORKER_THREAD, bus=bus
    )
    assert _row("d-f1post")["park_kind"] == PARK_KIND_AWAIT_REPLY


@pytest.mark.asyncio
async def test_f2_failed_pre_run_child_does_not_close_park(
    tmp_path: Path, _admit_stubs: MagicMock
) -> None:
    """F2: drain-503 child (failed, never started) must not stamp park_resumed_by."""
    _seed_running("d-f2", tmp_path=tmp_path)
    _record_generate(tmp_path, "d-f2")
    bus = _bus()
    await _park("d-f2", bus=bus, turns=_Turns(), tmp_path=tmp_path)
    _mark_completed("d-f2")
    ledger = CursorDispatchLedger.instance()
    # Simulate post-admit Draining503: row inserted, marked failed, never running.
    # Use a non-cN id so the reactor's first mint (d-f2-c1) does not collide.
    dead = CursorDispatchRequest(
        thread_id=_WORKER_THREAD,
        model="cursor/grok-4.7",
        dispatch_id="d-f2-phantom",
        execution_id="exec-d-f2",
        caller_agent="cursor",
        message="phantom",
        handoff_contract="freeform",
        resume_of="d-f2",
        admitted_via=ADMITTED_VIA_AWAIT_RESUME,
        work_key=_WORK_KEY,
    )
    ledger.admit(
        req=dead,
        fingerprint=ledger.fingerprint(dead),
        execution_id=dead.execution_id,
        caller_agent="cursor",
        resolved_model="grok-4.7",
        admission=CursorDispatchResponse(
            admitted=True,
            dispatch_id="d-f2-phantom",
            thread_id=_WORKER_THREAD,
            model_id="m",
        ),
        contract="freeform",
        source_repo=str(load_config().source_repo),
        lease_key="lease-f2",
        work_key=_WORK_KEY,
        identity_class="declared",
    )
    ledger.mark_terminal(dispatch_id="d-f2-phantom", terminal_status="failed")
    assert _row("d-f2-phantom")["started_at"] is None
    assert _row("d-f2-phantom")["status"] == "failed"

    turns = _Turns({_COORD_THREAD: [_reply_turn()]})
    summary = await _tick(bus=bus, turns=turns)
    assert summary.reconciled == []
    assert summary.admitted == [("d-f2", "d-f2-c1")]
    assert _row("d-f2")["park_resumed_by"] == "d-f2-c1"


@pytest.mark.asyncio
async def test_f2_gate_ignores_stamped_failed_pre_run_child(tmp_path: Path) -> None:
    """F2: stamped park_resumed_by naming a pre-run failure does not 409 a new child."""
    _seed_running("d-f2g", tmp_path=tmp_path)
    _record_generate(tmp_path, "d-f2g")
    await _park("d-f2g", bus=_bus(), turns=_Turns(), tmp_path=tmp_path)
    _mark_completed("d-f2g")
    ledger = CursorDispatchLedger.instance()
    with ledger._connect() as conn:
        conn.execute(
            "INSERT INTO cursor_sdk_dispatches "
            "(dispatch_id, fingerprint, thread_id, execution_id, resolved_model, "
            "message_present, status, resume_of, started_at) "
            "VALUES ('d-f2g-dead', 'fp', ?, 'e', 'm', 1, 'failed', 'd-f2g', NULL)",
            (_WORKER_THREAD,),
        )
        conn.execute(
            "UPDATE cursor_sdk_dispatches SET park_resumed_by='d-f2g-dead' "
            "WHERE dispatch_id='d-f2g'"
        )
    hand = CursorDispatchRequest(
        thread_id=_WORKER_THREAD,
        model="cursor/grok-4.7",
        dispatch_id="d-f2g-hand",
        execution_id="exec-hand",
        caller_agent="cursor",
        message="recover",
        handoff_contract="freeform",
        resume_of="d-f2g",
        work_key=_WORK_KEY,
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
                dispatch_id="d-f2g-hand",
                thread_id=_WORKER_THREAD,
                model_id="m",
            ),
            contract="freeform",
            source_repo=str(load_config().source_repo),
            lease_key="lease-f2g",
            work_key=_WORK_KEY,
            identity_class="declared",
        )
        is None
    )
    assert _row("d-f2g-hand") is not None


@pytest.mark.asyncio
async def test_f2_reactor_admit_inserts_then_503(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Gap: admit inserts the child then returns 503 — park stays open for retry."""
    from fastapi.responses import JSONResponse

    from services.git_integration_worker.routes import cursor_sdk as route_mod

    _seed_running("d-f2s", tmp_path=tmp_path)
    _record_generate(tmp_path, "d-f2s")
    await _park("d-f2s", bus=_bus(), turns=_Turns(), tmp_path=tmp_path)
    _mark_completed("d-f2s")

    async def _admit_then_503(req, *, cfg, controller, request=None):  # noqa: ANN001
        ledger = CursorDispatchLedger.instance()
        ledger.admit(
            req=req,
            fingerprint=ledger.fingerprint(req),
            execution_id=req.execution_id,
            caller_agent=req.caller_agent or "cursor",
            resolved_model="grok-4.7",
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
        ledger.mark_terminal(dispatch_id=req.dispatch_id, terminal_status="failed")
        return JSONResponse(
            status_code=503,
            content={"code": "Draining503", "message": "draining"},
        )

    monkeypatch.setattr(route_mod, "admit_cursor_dispatch", _admit_then_503)
    turns = _Turns({_COORD_THREAD: [_reply_turn()]})
    first = await _tick(bus=_bus(), turns=turns)
    assert first.admitted == []
    assert first.refused
    assert _row("d-f2s")["park_resumed_by"] is None
    assert _row("d-f2s-c1")["status"] == "failed"
    assert _row("d-f2s-c1")["started_at"] is None

    # Second tick with a healthy admit stub recovers.
    async def _ok(req, *, cfg, controller, request=None):  # noqa: ANN001
        ledger = CursorDispatchLedger.instance()
        ledger.admit(
            req=req,
            fingerprint=ledger.fingerprint(req),
            execution_id=req.execution_id,
            caller_agent=req.caller_agent or "cursor",
            resolved_model="grok-4.7",
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
        ledger.mark_running(dispatch_id=req.dispatch_id)
        return JSONResponse(status_code=200, content={"admitted": True})

    monkeypatch.setattr(route_mod, "admit_cursor_dispatch", _ok)
    second = await _tick(bus=_bus(), turns=turns)
    assert second.admitted == [("d-f2s", "d-f2s-c2")]


@pytest.mark.asyncio
async def test_f4_running_parent_not_in_open_await_rows(tmp_path: Path) -> None:
    """F4: park while still running is invisible to the reactor until terminal."""
    _seed_running("d-f4run", tmp_path=tmp_path)
    _record_generate(tmp_path, "d-f4run")
    assert await _park("d-f4run", bus=_bus(), turns=_Turns(), tmp_path=tmp_path) is True
    assert _row("d-f4run")["status"] == "running"
    assert open_await_rows() == []
    summary = await _tick(bus=_bus(), turns=_Turns({_COORD_THREAD: [_reply_turn()]}))
    assert summary.admitted == [] and summary.waiting == []
    _mark_completed("d-f4run")
    assert [r.dispatch_id for r in open_await_rows()] == ["d-f4run"]


@pytest.mark.asyncio
async def test_f4_bridge_death_await_parent_not_auto_resumed(
    tmp_path: Path,
) -> None:
    """F4: await_cdp_reply does not inherit park_for_restart eligibility exemption."""
    from services.git_integration_worker.cursor_sdk_resume import (
        resume_eligibility_reason,
    )

    _seed_running("d-f4bd", tmp_path=tmp_path)
    _record_generate(tmp_path, "d-f4bd")
    await _park("d-f4bd", bus=_bus(), turns=_Turns(), tmp_path=tmp_path)
    ledger = CursorDispatchLedger.instance()
    ledger.mark_terminal(dispatch_id="d-f4bd", terminal_status="failed")
    ledger.merge_record_json(
        dispatch_id="d-f4bd",
        patch={
            "bridge_death_partial_uri": "cortex://notes/system/threads/x.md",
            "resume_eligible": False,
            "bridge_death_degraded_reason": "spawn_enoent_missing_cwd",
        },
    )
    assert (
        resume_eligibility_reason(ledger, parent_id="d-f4bd")
        == "bridge_death_not_resume_eligible"
    )
    turns = _Turns({_COORD_THREAD: [_reply_turn()]})
    # Permanent? bridge_death_not_resume_eligible is not in _PERMANENT_INELIGIBLE —
    # it refuses until the cap expires. First tick refuses.
    summary = await _tick(bus=_bus(), turns=turns)
    assert summary.admitted == []
    assert summary.refused == [("d-f4bd", "bridge_death_not_resume_eligible")]


@pytest.mark.asyncio
async def test_f6_refusal_cap_expires_the_park(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """F6: after N refused attempts the park expires instead of retrying forever."""
    from services.git_integration_worker import cursor_sdk_await_reply as mod

    monkeypatch.setattr(mod, "_MAX_RESUME_ATTEMPTS", 2)
    _seed_running("d-f6", tmp_path=tmp_path)
    _record_generate(tmp_path, "d-f6")
    await _park("d-f6", bus=_bus(), turns=_Turns(), tmp_path=tmp_path)
    ledger = CursorDispatchLedger.instance()
    ledger.mark_terminal(dispatch_id="d-f6", terminal_status="failed")
    ledger.merge_record_json(
        dispatch_id="d-f6",
        patch={
            "bridge_death_partial_uri": "cortex://notes/x.md",
            "resume_eligible": False,
        },
    )
    turns = _Turns({_COORD_THREAD: [_reply_turn()]})
    bus = _bus()
    first = await _tick(bus=bus, turns=turns)
    assert first.refused == [("d-f6", "bridge_death_not_resume_eligible")]
    assert first.expired == []
    second = await _tick(bus=bus, turns=turns)
    assert second.refused == [("d-f6", "bridge_death_not_resume_eligible")]
    assert second.expired == []
    # With max=2, the third tick hits the refusal cap and expires (1f).
    third = await _tick(bus=bus, turns=turns)
    assert third.expired == ["d-f6"]
    assert json.loads(_row("d-f6")["record_json"])["park"].get("expired_at")


@pytest.mark.asyncio
async def test_f7_immediate_resume_scoped_to_parent(
    tmp_path: Path, _admit_stubs: MagicMock
) -> None:
    """F7: closeout pass resumes only the closing dispatch, with a real code_version."""
    _seed_running("d-f7a", tmp_path=tmp_path, work_key="friction:34156-a")
    _seed_running(
        "d-f7b", tmp_path=tmp_path, thread_id="14695", work_key="friction:34156-b"
    )
    _record_generate(tmp_path, "d-f7a")
    _record_generate(tmp_path, "d-f7b", execution_id=_EXEC2)
    bus = _bus()
    await _park("d-f7a", bus=bus, turns=_Turns(), tmp_path=tmp_path)
    await _park("d-f7b", bus=bus, turns=_Turns(), tmp_path=tmp_path)
    _mark_completed("d-f7a")
    _mark_completed("d-f7b")
    turns = _Turns(
        {
            _COORD_THREAD: [
                _reply_turn(),
                _reply_turn(execution_id=_EXEC2, turn_number=5),
            ]
        }
    )
    summary = await resume_await_parked_dispatches(
        cfg=load_config(),
        controller=_controller(),
        code_version="deadbeef",
        bus=bus,
        bus_turns_fn=turns,
        parent_dispatch_id="d-f7a",
    )
    assert summary.admitted == [("d-f7a", "d-f7a-c1")]
    assert _row("d-f7b")["park_resumed_by"] is None
    preamble = json.loads(_row("d-f7a-c1")["record_json"])["prompt_preamble"]
    assert "code_version deadbeef" in preamble
    assert "code_version closeout" not in preamble


@pytest.mark.asyncio
async def test_f8_mark_await_parked_does_not_reset_resumed_lineage(
    tmp_path: Path,
) -> None:
    """F8: a duplicate park stamp cannot clear park_resumed_by."""
    from services.git_integration_worker.cursor_sdk_await_reply import mark_await_parked

    _seed_running("d-f8", tmp_path=tmp_path)
    _record_generate(tmp_path, "d-f8")
    await _park("d-f8", bus=_bus(), turns=_Turns(), tmp_path=tmp_path)
    _mark_completed("d-f8")
    with CursorDispatchLedger.instance()._connect() as conn:
        conn.execute(
            "UPDATE cursor_sdk_dispatches SET park_resumed_by='d-f8-c1' "
            "WHERE dispatch_id='d-f8'"
        )
    again = mark_await_parked(
        dispatch_id="d-f8",
        awaited=fired_generates("d-f8", spool_dir=_spool(tmp_path)),
    )
    assert again is None
    assert _row("d-f8")["park_resumed_by"] == "d-f8-c1"


@pytest.mark.asyncio
async def test_f9_ttl_preamble_says_unknown_when_bus_unreachable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(AWAIT_REPLY_TTL_ENV, "60")
    _seed_running("d-f9ttl", tmp_path=tmp_path)
    _record_generate(tmp_path, "d-f9ttl")
    await _park("d-f9ttl", bus=_bus(), turns=_Turns(), tmp_path=tmp_path)
    _mark_completed("d-f9ttl")
    parked_at = datetime.fromisoformat(_row("d-f9ttl")["parked_at"])

    def _boom(_thread: str, _after: int) -> list[dict[str, Any]]:
        raise ConnectionError("bus down")

    summary = await _tick(
        bus=_bus(), turns=_boom, now=parked_at + timedelta(seconds=61)
    )
    assert summary.admitted == [("d-f9ttl", "d-f9ttl-c1")]
    preamble = json.loads(_row("d-f9ttl-c1")["record_json"])["prompt_preamble"]
    assert "(unknown)" in preamble and "bus unreachable" in preamble


def test_f9_ledger_path_matches_bridge_and_giw() -> None:
    from scripts.mcp_bridge_generate_ledger import generate_ledger_path
    from services.git_integration_worker.cursor_sdk_await_reply import _ledger_path

    spool = Path("/tmp/spool-f9")
    assert generate_ledger_path(spool, "a/b:c") == _ledger_path(spool, "a/b:c")


def test_f9_default_bus_turns_passes_after_turn(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """F9: GET /turns uses after_turn so a tip ``last`` cannot hide replies."""
    from services.git_integration_worker import cursor_sdk_await_reply as mod

    captured: dict[str, Any] = {}

    class _Resp:
        status_code = 200

        def json(self) -> dict[str, Any]:
            return {"turns": [{"turn_number": 4, "from": "web-anthropic"}]}

    class _Client:
        def __enter__(self) -> _Client:
            return self

        def __exit__(self, *_a: object) -> None:
            return None

        def get(self, path: str, *, params: dict[str, Any], headers: Any) -> _Resp:
            captured["path"] = path
            captured["params"] = params
            return _Resp()

    monkeypatch.setattr("transport_utils.make_sync_client", lambda *_a, **_k: _Client())
    turns = mod._default_bus_turns("14692", 3)
    assert captured["params"] == {"thread": "14692", "after_turn": 3}
    assert len(turns) == 1


@pytest.mark.asyncio
async def test_deliver_sdk_closeout_park_ordering_and_exception_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Gap: _deliver_sdk_closeout — park before terminal; exception ⇒ still terminal; F7 scope."""
    from implement_admission.spec import CloseoutStatus

    from services.git_integration_worker.cursor_sdk_closeout import SdkRunOutcome
    from services.git_integration_worker.cursor_sdk_closeout.closeout_records import (
        CloseoutDelivery,
    )
    from services.git_integration_worker.routes import cursor_sdk as route_mod

    monkeypatch.setenv(AWAIT_REPLY_FLAG_ENV, "1")
    _seed_running("d-close", tmp_path=tmp_path)
    _record_generate(tmp_path, "d-close")
    # Point steer spool at the test spool so outstanding_generates sees the fire.
    monkeypatch.setenv("ULG_STEER_SPOOL_DIR", str(_spool(tmp_path)))

    order: list[str] = []
    real_park = __import__(
        "services.git_integration_worker.cursor_sdk_await_reply", fromlist=["x"]
    ).maybe_await_park_at_terminal

    async def _track_park(**kw: Any) -> bool:
        order.append("park")
        return await real_park(**kw)

    real_promote = route_mod._mark_terminal_and_promote

    async def _track_promote(**kw: Any) -> None:
        order.append("promote")
        return await real_promote(**kw)

    monkeypatch.setattr(
        "services.git_integration_worker.cursor_sdk_await_reply.maybe_await_park_at_terminal",
        _track_park,
    )
    monkeypatch.setattr(route_mod, "_mark_terminal_and_promote", _track_promote)

    async def _prep(**_kw: Any) -> CloseoutDelivery:
        return CloseoutDelivery(
            body="status: complete\n",
            sidecar_ref=None,
            sidecar_path=None,
            full_result_bytes=10,
            closeout_status=CloseoutStatus.COMPLETE,
        )

    monkeypatch.setattr(route_mod, "prepare_closeout_delivery_async", _prep)
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_sdk_closeout.conductor_closeout_pager."
        "page_conductor_silence",
        AsyncMock(),
    )
    monkeypatch.setattr(route_mod, "emit_implement_closeout_trigger", AsyncMock())
    monkeypatch.setattr(
        CursorDispatchLedger,
        "read_wt_baseline",
        lambda self, **_kw: {"files": {}, "codes": {}},
    )
    monkeypatch.setattr(
        route_mod, "release_or_restore_for_child", AsyncMock(return_value="released")
    )
    monkeypatch.setattr(
        route_mod, "maybe_prune_worktree_on_terminal", lambda **_kw: None
    )
    monkeypatch.setattr(route_mod, "_promote_queued_for_lease", AsyncMock())
    monkeypatch.setattr(
        route_mod, "merge_conductor_closeout_hop_authority", lambda **_k: None
    )
    monkeypatch.setattr(route_mod, "_terminate_link", AsyncMock())
    monkeypatch.setattr(route_mod, "emit_sdk_worker_completed", lambda **_k: None)
    from services.git_integration_worker.cursor_sdk_await_reply import (
        AwaitResumeSummary,
    )

    resume_mock = AsyncMock(return_value=AwaitResumeSummary())

    async def _track_announce(**kw: Any) -> None:
        order.append("announce")
        return None

    monkeypatch.setattr(
        "services.git_integration_worker.cursor_sdk_await_reply.resume_await_parked_dispatches",
        resume_mock,
    )
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_sdk_await_reply.announce_await_parked",
        _track_announce,
    )

    bus = _bus()
    req = CursorDispatchRequest(
        thread_id=_WORKER_THREAD,
        model="cursor/grok-4.7",
        dispatch_id="d-close",
        execution_id="exec-d-close",
        caller_agent="cursor",
        message="packet",
        handoff_contract="freeform",
        work_key=_WORK_KEY,
    )
    await route_mod._deliver_sdk_closeout(
        req=req,
        source_repo=tmp_path / "repo",
        outcome=SdkRunOutcome(
            body="status: complete\n",
            status="finished",
            duration_ms=10,
            tool_call_count=1,
        ),
        degraded_reason=None,
        bus=bus,
        reply_to="dispatch",
        work_item_ref=_WORK_KEY,
        controller=_controller(),
    )
    assert order == ["park", "promote", "announce"]
    assert _row("d-close")["status"] == "completed"
    assert _row("d-close")["park_kind"] == PARK_KIND_AWAIT_REPLY
    # 1f: route wiring scopes resume and never passes code_version="closeout".
    resume_mock.assert_awaited_once()
    resume_kwargs = resume_mock.await_args.kwargs
    assert resume_kwargs["parent_dispatch_id"] == req.dispatch_id
    assert resume_kwargs["code_version"] != "closeout"

    # Exception path: park raises → still promoted.
    _seed_running(
        "d-close-x",
        tmp_path=tmp_path,
        thread_id="14696",
        work_key="friction:34156-x",
    )
    _record_generate(tmp_path, "d-close-x")

    async def _raise(**_kw: Any) -> bool:
        order.append("park-raise")
        raise RuntimeError("hook boom")

    monkeypatch.setattr(
        "services.git_integration_worker.cursor_sdk_await_reply.maybe_await_park_at_terminal",
        _raise,
    )
    order.clear()
    # Re-bind promote tracker
    monkeypatch.setattr(route_mod, "_mark_terminal_and_promote", _track_promote)
    req2 = CursorDispatchRequest(
        thread_id="14696",
        model="cursor/grok-4.7",
        dispatch_id="d-close-x",
        execution_id="exec-d-close-x",
        caller_agent="cursor",
        message="packet",
        handoff_contract="freeform",
        work_key="friction:34156-x",
    )
    await route_mod._deliver_sdk_closeout(
        req=req2,
        source_repo=tmp_path / "repo",
        outcome=SdkRunOutcome(
            body="status: complete\n",
            status="finished",
            duration_ms=10,
            tool_call_count=1,
        ),
        degraded_reason=None,
        bus=_bus(),
        reply_to="dispatch",
        work_item_ref="friction:34156-x",
        controller=_controller(),
    )
    assert "promote" in order
    assert _row("d-close-x")["status"] == "completed"


@pytest.mark.asyncio
async def test_1b_hand_path_phantom_child_reclaimed_by_reactor(
    tmp_path: Path, _admit_stubs: MagicMock
) -> None:
    """1b: hand-stamped child that failed before run is cleared; reactor resumes."""
    _seed_running("d-1b", tmp_path=tmp_path)
    _record_generate(tmp_path, "d-1b")
    await _park("d-1b", bus=_bus(), turns=_Turns(), tmp_path=tmp_path)
    _mark_completed("d-1b")
    ledger = CursorDispatchLedger.instance()
    hand = CursorDispatchRequest(
        thread_id=_WORKER_THREAD,
        model="cursor/grok-4.7",
        dispatch_id="d-1b-hand",
        execution_id="exec-hand",
        caller_agent="cursor",
        message="hand resume phantom",
        handoff_contract="freeform",
        resume_of="d-1b",
        work_key=_WORK_KEY,
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
                dispatch_id="d-1b-hand",
                thread_id=_WORKER_THREAD,
                model_id="m",
            ),
            contract="freeform",
            source_repo=str(load_config().source_repo),
            lease_key="lease-1b",
            work_key=_WORK_KEY,
            identity_class="declared",
        )
        is None
    )
    assert _row("d-1b")["park_resumed_by"] == "d-1b-hand"
    ledger.mark_terminal(dispatch_id="d-1b-hand", terminal_status="failed")
    assert _row("d-1b-hand")["started_at"] is None

    turns = _Turns({_COORD_THREAD: [_reply_turn()]})
    summary = await _tick(bus=_bus(), turns=turns)
    assert summary.admitted == [("d-1b", "d-1b-c1")]
    assert _row("d-1b")["park_resumed_by"] == "d-1b-c1"


@pytest.mark.asyncio
async def test_1d_started_then_failed_child_reconciles(
    tmp_path: Path,
) -> None:
    """1d: a child that ran then failed closes the park; no second resume."""
    _seed_running("d-1d", tmp_path=tmp_path)
    _record_generate(tmp_path, "d-1d")
    await _park("d-1d", bus=_bus(), turns=_Turns(), tmp_path=tmp_path)
    _mark_completed("d-1d")
    ledger = CursorDispatchLedger.instance()
    child = CursorDispatchRequest(
        thread_id=_WORKER_THREAD,
        model="cursor/grok-4.7",
        dispatch_id="d-1d-ran",
        execution_id="exec-d-1d",
        caller_agent="cursor",
        message="ran then failed",
        handoff_contract="freeform",
        resume_of="d-1d",
        admitted_via=ADMITTED_VIA_AWAIT_RESUME,
        work_key=_WORK_KEY,
    )
    ledger.admit(
        req=child,
        fingerprint=ledger.fingerprint(child),
        execution_id=child.execution_id,
        caller_agent="cursor",
        resolved_model="grok-4.7",
        admission=CursorDispatchResponse(
            admitted=True,
            dispatch_id="d-1d-ran",
            thread_id=_WORKER_THREAD,
            model_id="m",
        ),
        contract="freeform",
        source_repo=str(load_config().source_repo),
        lease_key="lease-1d",
        work_key=_WORK_KEY,
        identity_class="declared",
    )
    ledger.mark_running(dispatch_id="d-1d-ran")
    ledger.mark_terminal(dispatch_id="d-1d-ran", terminal_status="failed")
    assert _row("d-1d-ran")["started_at"] is not None
    # Crash window: stamp never written.
    assert _row("d-1d")["park_resumed_by"] is None

    turns = _Turns({_COORD_THREAD: [_reply_turn()]})
    summary = await _tick(bus=_bus(), turns=turns)
    assert summary.reconciled == [("d-1d", "d-1d-ran")]
    assert summary.admitted == []
    assert _row("d-1d")["park_resumed_by"] == "d-1d-ran"


def _stub_boot_host(monkeypatch: pytest.MonkeyPatch, turns: _Turns) -> None:
    """Keep startup off the host git/process surface; inject bus turns."""

    async def _reclaim() -> list[str]:
        return []

    monkeypatch.setattr(route_mod, "prune_stale_dispatch_homes", lambda: 0)
    monkeypatch.setattr(
        route_mod, "reap_orphan_worktrees", lambda **_k: ReapSweepResult()
    )
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_sdk_gate.reclaim_cross_lane_phantom_holders",
        _reclaim,
    )
    monkeypatch.setattr(
        route_mod,
        "reap_orphan_bridge_os",
        lambda _dispatch_id: BridgeReapResult(bridge_aborted=False),
    )
    monkeypatch.setattr(
        route_mod, "release_or_restore_for_child", AsyncMock(return_value="released")
    )
    monkeypatch.setattr(
        route_mod,
        "salvage_restart_survivor_worktree",
        lambda **_k: SimpleNamespace(
            pruned=False, salvaged=False, branch_retained=False
        ),
    )
    monkeypatch.setattr(
        route_mod, "_promote_queued_for_lease", AsyncMock(return_value=None)
    )
    monkeypatch.setattr(route_mod, "_resume_parked_rows", AsyncMock(return_value=None))
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_sdk_await_reply.CursorBusClient",
        lambda: _bus(),
    )
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_sdk_await_reply._default_bus_turns",
        turns,
    )


def _boot_app() -> SimpleNamespace:
    return SimpleNamespace(
        state=SimpleNamespace(
            worker_config=load_config(),
            admission_controller=_controller(),
            worker_version="boot-test",
        )
    )


@pytest.mark.asyncio
async def test_boot_admits_await_when_reply_landed_during_restart(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Breaks when a terminal await park is invisible until the stale sweeper.

    friction:37431 — GIW died after the await was durable. The reply is already
    on the bus at the next process start. Boot must admit -c1 without waiting
    out CURSOR_STALE_SWEEP_S.
    """
    _seed_running("d-boot", tmp_path=tmp_path)
    _record_generate(tmp_path, "d-boot")
    assert await _park("d-boot", bus=_bus(), turns=_Turns(), tmp_path=tmp_path)
    _mark_completed("d-boot")
    turns = _Turns({_COORD_THREAD: [_reply_turn()]})
    _stub_boot_host(monkeypatch, turns)

    await route_mod.startup_ledger_reconcile(_boot_app())

    assert _children("d-boot") == ["d-boot-c1"]
    child = _row("d-boot-c1")
    assert child is not None
    assert child["thread_id"] == _row("d-boot")["thread_id"]
    assert _row("d-boot")["park_resumed_by"] == "d-boot-c1"


@pytest.mark.asyncio
async def test_restart_then_reply_seals_running_await(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Breaks when boot rewrites a pre-terminal await as park_for_restart.

    friction:37431 — park columns are stamped while status is still running
    (NEST_CHAIN can refuse the drain park, so the row stays the live holder).
    A restart must keep park_kind=await_cdp_reply, and the reply that lands
    after boot must admit -c1 on the next await pass (sweeper tick ≤30s).
    """
    _seed_running("d-live", tmp_path=tmp_path)
    _record_generate(tmp_path, "d-live")
    assert await _park("d-live", bus=_bus(), turns=_Turns(), tmp_path=tmp_path)
    assert _row("d-live")["status"] == "running"
    assert _row("d-live")["park_kind"] == PARK_KIND_AWAIT_REPLY
    turns = _Turns()
    _stub_boot_host(monkeypatch, turns)

    await route_mod.startup_ledger_reconcile(_boot_app())

    row = _row("d-live")
    assert row is not None
    assert row["park_kind"] == PARK_KIND_AWAIT_REPLY
    assert row["status"] == "cancelled"
    assert [r.dispatch_id for r in open_park_rows()] == []
    assert [r.dispatch_id for r in open_await_rows()] == ["d-live"]
    assert _children("d-live") == []

    turns.turns = {_COORD_THREAD: [_reply_turn()]}
    await route_mod._resume_await_reply_rows(
        controller=_controller(),
        cfg=load_config(),
        code_version="boot-test",
    )
    assert _children("d-live") == ["d-live-c1"]
    assert _row("d-live-c1")["thread_id"] == _row("d-live")["thread_id"]
