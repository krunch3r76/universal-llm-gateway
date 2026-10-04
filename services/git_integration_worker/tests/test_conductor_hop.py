"""R3: conductor hop reactor (todo:conductor-hop-reactor)."""

from __future__ import annotations

import json
import re
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.git_integration_worker.cursor_dispatch_ledger import CursorDispatchLedger
from services.git_integration_worker.cursor_sdk_closeout.conductor_exit_reasons import (
    SKIP_GATE_LIVE_EXTERNAL,
    SKIP_GATE_PROBE_INDETERMINATE,
)
from services.git_integration_worker.cursor_sdk_closeout.conductor_hop import (
    SKIP_GATE_NEXT_ADMIT_BLOCKED,
    _hop_skip_gate,
    _scoreboard_entry_gate,
    _utc_closeout_instant,
    build_conductor_hop_idempotency_key,
    build_hop_team_dispatch_body,
    hop_owed,
    maybe_fire_conductor_hop_reactor,
    merge_conductor_closeout_hop_authority,
    post_conductor_hop_team_dispatch,
    release_deferred_conductor_hops,
)
from services.git_integration_worker.cursor_sdk_closeout.conductor_hop_budget import (
    HopBudgetConfig,
    evaluate_hop_budget,
    load_hop_budget_config,
)
from services.git_integration_worker.cursor_sdk_closeout.conductor_hop_watchdog import (
    maybe_fire_conductor_hop_watchdog,
)
from services.git_integration_worker.cursor_sdk_closeout.conductor_park_harvest import (
    park_harvest_continue_owed,
)
from services.git_integration_worker.cursor_sdk_ledger_hop import (
    hop_fields_from_record_json,
)
from services.git_integration_worker.models.cursor_api import (
    CursorDispatchRequest,
    CursorDispatchResponse,
)

pytestmark = pytest.mark.offline

_WORK_KEY = "todo:conductor-hop-fixture"
_ROW_HOP_CLOSEOUT = """\
status: complete
stop: ROW_HOP
hop_seq: 1
"""

_SPURIOUS_DONE_ROW_HOP_CLOSEOUT = """\
| G1 | Architecture / recon | DONE |
| G2 | Frame | DONE |
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
        "thread_id": "9964",
        "model": "cursor/composer-2.5",
        "dispatch_id": "pred-hop-1",
        "execution_id": "exec-pred-hop-1",
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
        patch={"contract": "conductor", "lane": "B"},
    )
    if record_patch:
        ledger.merge_record_json(dispatch_id=req.dispatch_id, patch=record_patch)


def _terminal_row(
    ledger: CursorDispatchLedger,
    *,
    dispatch_id: str = "pred-hop-1",
    closeout_tokens: list[str] | None = None,
    terminal_status: str = "completed",
    summoning_thread_id: str | None = None,
) -> dict:
    req = _req(dispatch_id=dispatch_id)
    _admit_conductor(ledger, req)
    patch: dict = {}
    if closeout_tokens is not None:
        patch["closeout_stop_tokens"] = closeout_tokens
    if summoning_thread_id:
        patch["summoning_thread_id"] = summoning_thread_id
    if patch:
        ledger.merge_record_json(dispatch_id=dispatch_id, patch=patch)
    ledger.mark_terminal(dispatch_id=dispatch_id, terminal_status=terminal_status)
    with ledger._connect() as conn:
        row = conn.execute(
            "SELECT * FROM cursor_sdk_dispatches WHERE dispatch_id=?",
            (dispatch_id,),
        ).fetchone()
    return {k: row[k] for k in row.keys()}


def test_hop_owed_true_for_planned_terminal_without_exit_tokens() -> None:
    ledger = CursorDispatchLedger.instance()
    row = _terminal_row(ledger, closeout_tokens=["ROW_HOP"])
    assert hop_owed(row, closeout_tokens=frozenset({"ROW_HOP"})) is True


def test_hop_owed_false_when_done_token() -> None:
    ledger = CursorDispatchLedger.instance()
    row = _terminal_row(ledger, closeout_tokens=["DONE"])
    assert hop_owed(row, closeout_tokens=frozenset({"DONE"})) is False


def test_hop_owed_true_when_spurious_table_done_excluded_from_designed() -> None:
    """Designed-stop scoping: table-cell DONE must not block successor admit."""
    ledger = CursorDispatchLedger.instance()
    _admit_conductor(ledger, _req())
    merge_conductor_closeout_hop_authority(
        dispatch_id="pred-hop-1",
        closeout_body=_SPURIOUS_DONE_ROW_HOP_CLOSEOUT,
        thread_id="9964",
    )
    ledger.mark_terminal(dispatch_id="pred-hop-1", terminal_status="completed")
    with ledger._connect() as conn:
        row = conn.execute(
            "SELECT * FROM cursor_sdk_dispatches WHERE dispatch_id='pred-hop-1'"
        ).fetchone()
    row = {k: row[k] for k in row.keys()}
    data = json.loads(row["record_json"])
    assert data.get("closeout_stop_tokens") == ["ROW_HOP"]
    assert "DONE" not in data.get("closeout_stop_tokens", [])
    assert hop_owed(row, closeout_tokens=frozenset({"ROW_HOP"})) is True


def test_hop_owed_false_when_exit_persist_token() -> None:
    ledger = CursorDispatchLedger.instance()
    row = _terminal_row(ledger, closeout_tokens=["ROW_PINNED"])
    assert hop_owed(row, closeout_tokens=frozenset({"ROW_PINNED"})) is False


_CONSULT_PENDING_WAIT_CLOSEOUT = """\
status: complete
stop: CONSULT_PENDING
CONSULT_PENDING
execution_id: exec-abc
poll_hint: wait 5s
NEXT_ADMIT: G5
"""


def test_hop_owed_false_when_consult_pending_wait_p24() -> None:
    """P2.4: CONSULT_PENDING wait blocks hop like degraded_reasons."""
    ledger = CursorDispatchLedger.instance()
    _admit_conductor(ledger, _req())
    merge_conductor_closeout_hop_authority(
        dispatch_id="pred-hop-1",
        closeout_body=_CONSULT_PENDING_WAIT_CLOSEOUT,
        thread_id="9964",
    )
    ledger.mark_terminal(dispatch_id="pred-hop-1", terminal_status="completed")
    with ledger._connect() as conn:
        row = conn.execute(
            "SELECT * FROM cursor_sdk_dispatches WHERE dispatch_id='pred-hop-1'"
        ).fetchone()
    row = {k: row[k] for k in row.keys()}
    assert hop_owed(row, closeout_tokens=frozenset({"CONSULT_PENDING"})) is False


def test_hop_owed_false_when_successor_already_stamped() -> None:
    ledger = CursorDispatchLedger.instance()
    row = _terminal_row(ledger, closeout_tokens=["ROW_HOP"])
    ledger.merge_record_json(
        dispatch_id="pred-hop-1",
        patch={"hop_successor": "already-admitted"},
    )
    with ledger._connect() as conn:
        refreshed = conn.execute(
            "SELECT * FROM cursor_sdk_dispatches WHERE dispatch_id='pred-hop-1'"
        ).fetchone()
    row = {k: refreshed[k] for k in refreshed.keys()}
    assert hop_owed(row, closeout_tokens=frozenset({"ROW_HOP"})) is False


def test_hop_owed_false_when_cancel_discard() -> None:
    """a:37149 — cancel_discard kills the mission; hop must not be owed.

    Specimen class: mid-run kill (cancelled, empty tokens) with
    park_kind=cancel_discard. Without the gate, hop_owed is True (silent hop).
    """
    ledger = CursorDispatchLedger.instance()
    row = _terminal_row(
        ledger,
        closeout_tokens=[],
        terminal_status="cancelled",
    )
    assert hop_owed(row, closeout_tokens=frozenset()) is True
    with ledger._connect() as conn:
        conn.execute(
            "UPDATE cursor_sdk_dispatches SET park_kind='cancel_discard' "
            "WHERE dispatch_id='pred-hop-1'"
        )
        refreshed = conn.execute(
            "SELECT * FROM cursor_sdk_dispatches WHERE dispatch_id='pred-hop-1'"
        ).fetchone()
    row = {k: refreshed[k] for k in refreshed.keys()}
    assert row["park_kind"] == "cancel_discard"
    assert hop_owed(row, closeout_tokens=frozenset()) is False
    assert hop_owed(row, closeout_tokens=frozenset({"ROW_HOP"})) is False


@pytest.mark.asyncio
async def test_reactor_skips_successor_after_cancel_discard() -> None:
    """a:37149 — reactor must not POST a hop after cancel_discard."""
    ledger = CursorDispatchLedger.instance()
    _terminal_row(
        ledger,
        closeout_tokens=[],
        terminal_status="cancelled",
        summoning_thread_id="9638",
    )
    with ledger._connect() as conn:
        conn.execute(
            "UPDATE cursor_sdk_dispatches SET park_kind='cancel_discard' "
            "WHERE dispatch_id='pred-hop-1'"
        )
    post_mock = AsyncMock(return_value=(True, {"dispatch_id": "should-not-admit"}))
    skipped_gates: list[str] = []
    with patch(
        "services.git_integration_worker.cursor_sdk_closeout.conductor_hop."
        "post_conductor_hop_team_dispatch",
        post_mock,
    ):
        with patch(
            "services.git_integration_worker.cursor_sdk_closeout.conductor_hop."
            "emit_frontier_sdk_conductor_hop_skipped",
            side_effect=lambda **kw: skipped_gates.append(kw["gate"]),
        ):
            await maybe_fire_conductor_hop_reactor(dispatch_id="pred-hop-1")
    post_mock.assert_not_called()
    assert "cancel_discard" in skipped_gates
    with ledger._connect() as conn:
        row = conn.execute(
            "SELECT record_json FROM cursor_sdk_dispatches WHERE dispatch_id='pred-hop-1'"
        ).fetchone()
    assert hop_fields_from_record_json(row["record_json"]).get("hop_successor") is None


def test_merge_closeout_stamps_hop_declared_and_tokens() -> None:
    ledger = CursorDispatchLedger.instance()
    _admit_conductor(ledger, _req())
    merge_conductor_closeout_hop_authority(
        dispatch_id="pred-hop-1",
        closeout_body=_ROW_HOP_CLOSEOUT,
        thread_id="9964",
    )
    with ledger._connect() as conn:
        row = conn.execute(
            "SELECT record_json FROM cursor_sdk_dispatches WHERE dispatch_id='pred-hop-1'"
        ).fetchone()
    fields = hop_fields_from_record_json(row["record_json"])
    assert fields.get("hop_declared") is True
    data = json.loads(row["record_json"])
    assert "ROW_HOP" in data.get("closeout_stop_tokens", [])


_PARKED_HARVEST_CLOSEOUT = """\
status: complete
stop: PARKED_TRANSPORT
CONSULT_PENDING
execution_id: exec-abc
poll_hint: wait
NEXT_ADMIT: harvest G1
"""

_NONE_NEXT_ADMIT_CLOSEOUT = """\
status: complete
stop: PARKED_TRANSPORT
NEXT_ADMIT: none
"""

_OMITTED_NEXT_ADMIT_PARKED_CLOSEOUT = """\
status: complete
stop: PARKED_TRANSPORT
CONSULT_PENDING
execution_id: exec-abc
poll_hint: wait
"""


def test_merge_closeout_stamps_closeout_turn_and_harvest_owed() -> None:
    """L1-1: production path stamps closeout_turn + closeout_harvest_owed."""
    ledger = CursorDispatchLedger.instance()
    _admit_conductor(ledger, _req())
    merge_conductor_closeout_hop_authority(
        dispatch_id="pred-hop-1",
        closeout_body=_PARKED_HARVEST_CLOSEOUT,
        thread_id="9964",
        closeout_turn=48,
    )
    with ledger._connect() as conn:
        row = conn.execute(
            "SELECT record_json FROM cursor_sdk_dispatches WHERE dispatch_id='pred-hop-1'"
        ).fetchone()
    data = json.loads(row["record_json"])
    assert data.get("closeout_turn") == 48
    assert data.get("closeout_harvest_owed") is True


def test_merge_closeout_harvest_owed_false_when_next_admit_none() -> None:
    """L1-2: NEXT_ADMIT:none ⇒ closeout_harvest_owed False."""
    ledger = CursorDispatchLedger.instance()
    _admit_conductor(ledger, _req())
    merge_conductor_closeout_hop_authority(
        dispatch_id="pred-hop-1",
        closeout_body=_NONE_NEXT_ADMIT_CLOSEOUT,
        thread_id="9964",
        closeout_turn=48,
    )
    with ledger._connect() as conn:
        row = conn.execute(
            "SELECT record_json FROM cursor_sdk_dispatches WHERE dispatch_id='pred-hop-1'"
        ).fetchone()
    data = json.loads(row["record_json"])
    assert data.get("closeout_harvest_owed") is False


def test_merge_closeout_harvest_owed_true_when_next_admit_omitted() -> None:
    """L1-2b: grader envelope omitting NEXT_ADMIT still owes park harvest."""
    ledger = CursorDispatchLedger.instance()
    _admit_conductor(ledger, _req())
    merge_conductor_closeout_hop_authority(
        dispatch_id="pred-hop-1",
        closeout_body=_OMITTED_NEXT_ADMIT_PARKED_CLOSEOUT,
        thread_id="9964",
        closeout_turn=48,
    )
    with ledger._connect() as conn:
        row = conn.execute(
            "SELECT record_json FROM cursor_sdk_dispatches WHERE dispatch_id='pred-hop-1'"
        ).fetchone()
    data = json.loads(row["record_json"])
    assert data.get("closeout_harvest_owed") is True


def test_external_gate_hop_verdict_body_overrides_false_stamp() -> None:
    """Probe fails closed when body still owes harvest despite stale stamp."""
    from services.git_integration_worker.cursor_sdk_closeout.conductor_exit_reasons import (
        external_gate_hop_verdict,
    )

    row = {
        "thread_id": "9964",
        "record_json": json.dumps(
            {
                "closeout_harvest_owed": False,
                "closeout_body": _OMITTED_NEXT_ADMIT_PARKED_CLOSEOUT,
                "summoning_thread_id": "9638",
            }
        ),
    }
    with patch(
        "services.git_integration_worker.cursor_sdk_closeout.conductor_exit_reasons.read_external_gate_lane_snapshot",
        return_value={},
    ):
        verdict, skip_gate = external_gate_hop_verdict(row)
    assert verdict == "indeterminate_closed"
    assert skip_gate == SKIP_GATE_PROBE_INDETERMINATE


def test_build_hop_team_dispatch_body_clones_predecessor() -> None:
    ledger = CursorDispatchLedger.instance()
    row = _terminal_row(ledger, closeout_tokens=["ROW_HOP"])
    body = build_hop_team_dispatch_body(row)
    assert body is not None
    assert body["op"] == "generate"
    assert body["seat"] == "cursor-sdk"
    assert body["caller_agent"] == "conductor-hop"
    assert body["reuse_thread"] == "9964"
    assert "dispatch_thread_id" not in body
    assert body["generation_options"]["summoning_thread_id_unresolved"] is True
    assert body["source_ref"] == _WORK_KEY
    assert "packet_kind" not in body
    assert body["lane"] == "B"
    assert str(body["model"]).startswith("cursor/")
    assert body["generation_options"][
        "idempotency_key"
    ] == build_conductor_hop_idempotency_key("pred-hop-1")


def test_hop_wire_body_is_a_legal_stargate_generate() -> None:
    """Contract: the hop body plus the reserved dispatch_id must validate.

    Stargate's generate model forbids extra keys. Two fields the hop sender
    added without a matching field (``packet_kind``, then ``dispatch_id``)
    each stopped every conductor at its first hop with a 400.
    """
    from systems.frontier_consult.route import TeamDispatchGenerateBody

    ledger = CursorDispatchLedger.instance()
    row = _terminal_row(
        ledger, closeout_tokens=["ROW_HOP"], summoning_thread_id="13511"
    )
    body = build_hop_team_dispatch_body(row)
    assert body is not None
    wire = dict(body)
    wire["dispatch_id"] = "hop-admit-reserved-1"
    parsed = TeamDispatchGenerateBody.model_validate(wire)
    assert parsed.dispatch_id == "hop-admit-reserved-1"
    assert parsed.hop_from == "pred-hop-1"
    assert parsed.dispatch_thread_id == "13511"
    assert body["hop_seq"] == 2
    assert body["hop_reason"] == "planned"
    assert body["hop_from"] == "pred-hop-1"
    assert "resume_of" not in body


_HOP_SUCCESSOR_DISPATCH_ID = re.compile(r"^[0-9a-f]{12}-[0-9a-f]{8}$")


@pytest.mark.asyncio
async def test_omitted_dispatch_id_mints_12hex_8hex(monkeypatch) -> None:
    """Breaks when a hop with no dispatch_id posts a bare uuid4.

    Reactor and watchdog both call this with the body from
    ``build_hop_team_dispatch_body``, which omits ``dispatch_id``. That UUID
    shape is an execution_id, so nest_under has to read the ledger. A reserved
    id already on the body is left unchanged.
    """
    ledger = CursorDispatchLedger.instance()
    ledger.mark_terminal(dispatch_id="pred-shape-1", terminal_status="completed")
    ledger.mark_terminal(dispatch_id="pred-shape-2", terminal_status="completed")
    posted: list[dict[str, Any]] = []

    async def _post(_endpoint: str, *, json: dict[str, Any]) -> MagicMock:
        posted.append(json)
        resp = MagicMock()
        resp.status_code = 202
        resp.json.return_value = {
            "dispatch_id": json["dispatch_id"],
            "status": "queued",
        }
        return resp

    client = MagicMock()
    client.post = _post

    async def _enter(*_a: object, **_k: object) -> MagicMock:
        return client

    async def _exit(*_a: object, **_k: object) -> None:
        return None

    holder = MagicMock()
    holder.__aenter__ = _enter
    holder.__aexit__ = _exit
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_sdk_closeout.conductor_hop.make_async_client",
        lambda *_a, **_k: holder,
    )

    ok, detail = await post_conductor_hop_team_dispatch(
        {"op": "generate", "hop_from": "pred-shape-1", "seat": "cursor-sdk"},
    )
    assert ok is True
    minted = posted[0]["dispatch_id"]
    assert _HOP_SUCCESSOR_DISPATCH_ID.fullmatch(minted)
    with pytest.raises(ValueError):
        uuid.UUID(minted)
    assert detail["dispatch_id"] == minted
    with ledger._connect() as conn:
        claimed = conn.execute(
            "SELECT serviced_admit FROM cursor_dispatch_stop_service WHERE stop_id=?",
            ("pred-shape-1",),
        ).fetchone()
    assert claimed["serviced_admit"] == minted

    reserved = "caller-reserved-hop-id"
    ok_reserved, _detail_reserved = await post_conductor_hop_team_dispatch(
        {
            "op": "generate",
            "hop_from": "pred-shape-2",
            "seat": "cursor-sdk",
            "dispatch_id": reserved,
        },
    )
    assert ok_reserved is True
    assert posted[1]["dispatch_id"] == reserved


def test_build_hop_team_dispatch_body_carries_model_knobs() -> None:
    ledger = CursorDispatchLedger.instance()
    knobs = {"effort": "low", "fast": "true"}
    row = _terminal_row(ledger, closeout_tokens=["ROW_HOP"])
    ledger.merge_record_json(dispatch_id="pred-hop-1", patch={"model_knobs": knobs})
    with ledger._connect() as conn:
        refreshed = conn.execute(
            "SELECT * FROM cursor_sdk_dispatches WHERE dispatch_id='pred-hop-1'"
        ).fetchone()
    row = {k: refreshed[k] for k in refreshed.keys()}
    body = build_hop_team_dispatch_body(row)
    assert body is not None
    assert body["model_knobs"] == knobs


def test_admit_record_json_stores_policy_filled_effort() -> None:
    from systems.frontier_consult.cursor_sdk_alignment import align_cursor_knobs

    ledger = CursorDispatchLedger.instance()
    alignment = align_cursor_knobs(
        resolved_model="cursor/grok-4.7",
        contract="conductor",
        model_knobs={"fast": "true"},
    )
    assert alignment.aligned_knobs["effort"] == "low"
    req = _req(model_knobs=alignment.aligned_knobs)
    _admit_conductor(ledger, req)
    with ledger._connect() as conn:
        raw = conn.execute(
            "SELECT record_json FROM cursor_sdk_dispatches WHERE dispatch_id='pred-hop-1'"
        ).fetchone()[0]
    assert json.loads(raw)["model_knobs"]["effort"] == "low"
    assert json.loads(raw)["model_knobs"]["fast"] == "true"


def test_park_resume_request_carries_model_knobs() -> None:
    from services.git_integration_worker.cursor_sdk_park_ledger import ParkRow
    from services.git_integration_worker.cursor_sdk_park_resume import (
        build_park_resume_request,
    )

    knobs = {"effort": "low", "fast": "true"}
    row = ParkRow(
        dispatch_id="park-1",
        thread_id="9964",
        execution_id="exec-park-1",
        caller_agent="cursor",
        resolved_model="cursor/grok-4.7",
        status="parked",
        terminal_status=None,
        sdk_agent_id=None,
        state_root=None,
        source_ref="todo:park",
        work_key="todo:park",
        contract="conductor",
        packet_path=None,
        park_kind="restart",
        park_intent_id="intent-1",
        parked_at="2026-09-30T00:00:00+00:00",
        park_resumed_by=None,
        park_expires_at=None,
        record_json=json.dumps(
            {
                "model_knobs": knobs,
                "model": "cursor/grok-4.7",
                "message": "continue",
                "park": {},
            }
        ),
    )
    child = build_park_resume_request(row, attempt=1, code_version="test")
    assert child.model_knobs == knobs


def test_build_hop_team_dispatch_body_routing_model_from_record_json() -> None:
    ledger = CursorDispatchLedger.instance()
    row = _terminal_row(ledger, closeout_tokens=["ROW_HOP"])
    ledger.merge_record_json(
        dispatch_id="pred-hop-1",
        patch={"model": "cursor/composer-2.5"},
    )
    with ledger._connect() as conn:
        refreshed = conn.execute(
            "SELECT * FROM cursor_sdk_dispatches WHERE dispatch_id='pred-hop-1'"
        ).fetchone()
    row = {k: refreshed[k] for k in refreshed.keys()}
    body = build_hop_team_dispatch_body(row)
    assert body is not None
    assert body["model"] == "cursor/composer-2.5"


def test_build_hop_team_dispatch_body_source_ref_from_record_json_only() -> None:
    ledger = CursorDispatchLedger.instance()
    row = _terminal_row(ledger, closeout_tokens=["ROW_HOP"])
    with ledger._connect() as conn:
        conn.execute(
            "UPDATE cursor_sdk_dispatches SET source_ref=NULL, work_key=NULL "
            "WHERE dispatch_id='pred-hop-1'"
        )
    ledger.merge_record_json(
        dispatch_id="pred-hop-1",
        patch={"source_ref": _WORK_KEY},
    )
    with ledger._connect() as conn:
        refreshed = conn.execute(
            "SELECT * FROM cursor_sdk_dispatches WHERE dispatch_id='pred-hop-1'"
        ).fetchone()
    row = {k: refreshed[k] for k in refreshed.keys()}
    body = build_hop_team_dispatch_body(row)
    assert body is not None
    assert body["source_ref"] == _WORK_KEY


def test_merge_uses_outcome_body_not_json_envelope() -> None:
    from services.git_integration_worker.cursor_sdk_closeout.closeout_records import (
        SdkRunOutcome,
    )
    from services.git_integration_worker.cursor_sdk_closeout.implement_body import (
        build_implement_closeout_body,
    )

    ledger = CursorDispatchLedger.instance()
    _admit_conductor(ledger, _req())
    outcome = SdkRunOutcome(
        body=_ROW_HOP_CLOSEOUT,
        status="finished",
        duration_ms=100,
        tool_call_count=1,
    )
    json_body = build_implement_closeout_body(
        dispatch_id="pred-hop-1",
        outcome=outcome,
        degraded_reason="conductor_row_hop",
        sidecar_ref="workspaces://x/sidecar.md",
        result_bytes=100,
        thread_id="9964",
        work_item_ref=_WORK_KEY,
    )
    assert "ROW_HOP" not in json_body
    merge_conductor_closeout_hop_authority(
        dispatch_id="pred-hop-1",
        closeout_body=outcome.body,
        thread_id="9964",
    )
    with ledger._connect() as conn:
        row = conn.execute(
            "SELECT record_json FROM cursor_sdk_dispatches WHERE dispatch_id='pred-hop-1'"
        ).fetchone()
    data = json.loads(row["record_json"])
    assert "ROW_HOP" in data.get("closeout_stop_tokens", [])
    assert data.get("hop_declared") is True


@pytest.mark.asyncio
async def test_maybe_fire_reactor_stamps_successor_on_admit() -> None:
    ledger = CursorDispatchLedger.instance()
    _terminal_row(ledger, closeout_tokens=["ROW_HOP"], summoning_thread_id="9638")
    with patch(
        "services.git_integration_worker.cursor_sdk_closeout.conductor_hop.post_conductor_hop_team_dispatch",
        AsyncMock(return_value=(True, {"dispatch_id": "succ-hop-2"})),
    ):
        await maybe_fire_conductor_hop_reactor(dispatch_id="pred-hop-1")
    with ledger._connect() as conn:
        row = conn.execute(
            "SELECT record_json FROM cursor_sdk_dispatches WHERE dispatch_id='pred-hop-1'"
        ).fetchone()
    fields = hop_fields_from_record_json(row["record_json"])
    assert fields.get("hop_successor") == "succ-hop-2"


@pytest.mark.asyncio
async def test_reactor_exception_does_not_block_second_hop_attempt() -> None:
    """R-4: reactor POST failure is recorded; idempotent re-entry still works."""
    ledger = CursorDispatchLedger.instance()
    _terminal_row(ledger, closeout_tokens=["ROW_HOP"], summoning_thread_id="9638")
    with patch(
        "services.git_integration_worker.cursor_sdk_closeout.conductor_hop.post_conductor_hop_team_dispatch",
        AsyncMock(
            side_effect=[
                RuntimeError("relay down"),
                (True, {"dispatch_id": "succ-hop-3"}),
            ]
        ),
    ):
        with pytest.raises(RuntimeError):
            await maybe_fire_conductor_hop_reactor(dispatch_id="pred-hop-1")
        await maybe_fire_conductor_hop_reactor(dispatch_id="pred-hop-1")
    with ledger._connect() as conn:
        row = conn.execute(
            "SELECT record_json FROM cursor_sdk_dispatches WHERE dispatch_id='pred-hop-1'"
        ).fetchone()
    fields = hop_fields_from_record_json(row["record_json"])
    assert fields.get("hop_successor") == "succ-hop-3"


@pytest.mark.asyncio
async def test_maybe_fire_emits_skipped_when_not_conductor() -> None:
    ledger = CursorDispatchLedger.instance()
    req = _req(dispatch_id="impl-row-1", message="implement task")
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
        contract="implement",
        source_repo="/repo",
        lease_key="/repo",
        source_ref="todo:other",
    )
    ledger.mark_terminal(dispatch_id="impl-row-1", terminal_status="completed")
    with patch(
        "services.git_integration_worker.cursor_sdk_closeout.conductor_hop.emit_frontier_sdk_conductor_hop_skipped",
    ) as skipped:
        await maybe_fire_conductor_hop_reactor(dispatch_id="impl-row-1")
    skipped.assert_called_once()
    assert skipped.call_args.kwargs["gate"] == "not_conductor_row"


@pytest.mark.asyncio
async def test_maybe_fire_reactor_stamps_admit_error_on_failure() -> None:
    ledger = CursorDispatchLedger.instance()
    _terminal_row(ledger, closeout_tokens=["ROW_HOP"], summoning_thread_id="9638")
    with patch(
        "services.git_integration_worker.cursor_sdk_closeout.conductor_hop.post_conductor_hop_team_dispatch",
        AsyncMock(return_value=(False, {"status_code": 503, "error": "down"})),
    ):
        await maybe_fire_conductor_hop_reactor(dispatch_id="pred-hop-1")
    with ledger._connect() as conn:
        row = conn.execute(
            "SELECT record_json FROM cursor_sdk_dispatches WHERE dispatch_id='pred-hop-1'"
        ).fetchone()
    fields = hop_fields_from_record_json(row["record_json"])
    assert "hop_admit_error" in fields
    assert fields.get("hop_successor") is None


def _live_gate_snap(*, parent_thread: str = "9638") -> dict:
    return {
        "observed_at": "2026-09-05T00:00:00+00:00",
        "rows": [
            {
                "execution_id": "exec-ext-gate",
                "parent_thread": parent_thread,
                "status": "running",
                "stream_state": "running",
                "purpose": "review",
            }
        ],
    }


def _live_gate_snap_seated_only(*, parent_thread: str = "9638") -> dict:
    return {
        "observed_at": "2026-09-05T00:00:00+00:00",
        "rows": [],
        "seated_rows": [
            {
                "execution_id": "exec-seated-gate",
                "parent_thread": parent_thread,
                "seat_state": "active",
                "stream_state": "none",
                "purpose": "review",
                "registration_id": "reg-gate",
            }
        ],
    }


def _live_operator_proxy_snap(*, parent_thread: str = "9638") -> dict:
    return {
        "observed_at": "2026-09-05T00:00:00+00:00",
        "rows": [
            {
                "execution_id": "exec-ext-gate",
                "parent_thread": parent_thread,
                "status": "running",
                "stream_state": "running",
                "purpose": "operator-proxy",
            }
        ],
    }


def test_external_gate_clear_when_harvest_not_owed_despite_live_stream() -> None:
    from services.git_integration_worker.cursor_sdk_closeout.conductor_exit_reasons import (
        external_gate_hop_verdict,
    )

    ledger = CursorDispatchLedger.instance()
    row = _terminal_row(ledger, closeout_tokens=["ROW_HOP"])
    ledger.merge_record_json(
        dispatch_id="pred-hop-1",
        patch={"summoning_thread_id": "9638", "closeout_harvest_owed": False},
    )
    with ledger._connect() as conn:
        refreshed = conn.execute(
            "SELECT * FROM cursor_sdk_dispatches WHERE dispatch_id='pred-hop-1'"
        ).fetchone()
    row = {k: refreshed[k] for k in refreshed.keys()}
    with patch(
        "services.git_integration_worker.cursor_sdk_closeout.conductor_exit_reasons.read_external_gate_lane_snapshot",
        return_value=_live_operator_proxy_snap(),
    ):
        verdict, skip_gate = external_gate_hop_verdict(row)
        assert verdict == "clear"
        assert skip_gate is None
        assert hop_owed(row, closeout_tokens=frozenset({"ROW_HOP"})) is True


def test_external_gate_live_when_harvest_owed_and_other_execution() -> None:
    from services.git_integration_worker.cursor_sdk_closeout.conductor_exit_reasons import (
        external_gate_hop_verdict,
    )

    ledger = CursorDispatchLedger.instance()
    row = _terminal_row(ledger, closeout_tokens=["ROW_HOP"])
    ledger.merge_record_json(
        dispatch_id="pred-hop-1",
        patch={"summoning_thread_id": "9638", "closeout_harvest_owed": True},
    )
    with ledger._connect() as conn:
        refreshed = conn.execute(
            "SELECT * FROM cursor_sdk_dispatches WHERE dispatch_id='pred-hop-1'"
        ).fetchone()
    row = {k: refreshed[k] for k in refreshed.keys()}
    with patch(
        "services.git_integration_worker.cursor_sdk_closeout.conductor_exit_reasons.read_external_gate_lane_snapshot",
        return_value=_live_gate_snap(),
    ):
        verdict, skip_gate = external_gate_hop_verdict(row)
        assert verdict == "live"
        assert skip_gate == SKIP_GATE_LIVE_EXTERNAL
        assert hop_owed(row, closeout_tokens=frozenset({"ROW_HOP"})) is False


def test_external_gate_clear_when_only_own_execution_live() -> None:
    from services.git_integration_worker.cursor_sdk_closeout.conductor_exit_reasons import (
        external_gate_hop_verdict,
    )

    ledger = CursorDispatchLedger.instance()
    row = _terminal_row(ledger, closeout_tokens=["ROW_HOP"])
    ledger.merge_record_json(
        dispatch_id="pred-hop-1",
        patch={"summoning_thread_id": "9638", "closeout_harvest_owed": True},
    )
    with ledger._connect() as conn:
        refreshed = conn.execute(
            "SELECT * FROM cursor_sdk_dispatches WHERE dispatch_id='pred-hop-1'"
        ).fetchone()
    row = {k: refreshed[k] for k in refreshed.keys()}
    own_exec = str(row.get("execution_id") or "")
    snap = {
        "observed_at": "2026-09-05T00:00:00+00:00",
        "rows": [
            {
                "execution_id": own_exec,
                "parent_thread": "9638",
                "stream_state": "running",
                "purpose": "review",
            }
        ],
    }
    with patch(
        "services.git_integration_worker.cursor_sdk_closeout.conductor_exit_reasons.read_external_gate_lane_snapshot",
        return_value=snap,
    ):
        verdict, skip_gate = external_gate_hop_verdict(row)
        assert verdict == "clear"
        assert skip_gate is None


def test_live_external_gate_reads_stream_state_not_seat() -> None:
    """L2-AC-h: external gate blocks on live stream_state only."""
    from services.git_integration_worker.cursor_sdk_closeout.conductor_exit_reasons import (
        live_external_gate_for_lane,
    )

    assert (
        live_external_gate_for_lane(
            {
                "rows": [],
                "seated_rows": [
                    {
                        "execution_id": "exec-seated",
                        "parent_thread": "9638",
                        "seat_state": "active",
                        "stream_state": "none",
                        "purpose": "review",
                        "registration_id": "reg-seated",
                    }
                ],
            },
            "9638",
        )
        is False
    )
    assert (
        live_external_gate_for_lane(
            {
                "rows": [
                    {
                        "execution_id": "exec-live",
                        "parent_thread": "9638",
                        "stream_state": "running",
                        "purpose": "review",
                    }
                ],
            },
            "9638",
        )
        is True
    )
    exec_id = "exec-dead"
    assert (
        live_external_gate_for_lane(
            {
                "rows": [
                    {
                        "execution_id": exec_id,
                        "parent_thread": "9638",
                        "stream_state": f"terminal:{exec_id}",
                        "purpose": "review",
                    }
                ],
            },
            "9638",
        )
        is False
    )


def test_hop_owed_true_when_seated_only_without_live_stream() -> None:
    """R2′: seated identity without live stream does not block external gate."""
    ledger = CursorDispatchLedger.instance()
    row = _terminal_row(ledger, closeout_tokens=["ROW_HOP"])
    ledger.merge_record_json(
        dispatch_id="pred-hop-1",
        patch={"summoning_thread_id": "9638", "closeout_harvest_owed": False},
    )
    with ledger._connect() as conn:
        refreshed = conn.execute(
            "SELECT * FROM cursor_sdk_dispatches WHERE dispatch_id='pred-hop-1'"
        ).fetchone()
    row = {k: refreshed[k] for k in refreshed.keys()}
    with patch(
        "services.git_integration_worker.cursor_sdk_closeout.conductor_exit_reasons.read_external_gate_lane_snapshot",
        return_value=_live_gate_snap_seated_only(),
    ):
        assert hop_owed(row, closeout_tokens=frozenset({"ROW_HOP"})) is True


def test_hop_owed_true_when_live_external_gate_and_harvest_not_owed() -> None:
    ledger = CursorDispatchLedger.instance()
    row = _terminal_row(ledger, closeout_tokens=["ROW_HOP"])
    ledger.merge_record_json(
        dispatch_id="pred-hop-1",
        patch={"summoning_thread_id": "9638", "closeout_harvest_owed": False},
    )
    with ledger._connect() as conn:
        refreshed = conn.execute(
            "SELECT * FROM cursor_sdk_dispatches WHERE dispatch_id='pred-hop-1'"
        ).fetchone()
    row = {k: refreshed[k] for k in refreshed.keys()}
    with patch(
        "services.git_integration_worker.cursor_sdk_closeout.conductor_exit_reasons.read_external_gate_lane_snapshot",
        return_value=_live_gate_snap(),
    ):
        assert hop_owed(row, closeout_tokens=frozenset({"ROW_HOP"})) is True


def test_hop_owed_false_when_live_external_gate_and_harvest_owed() -> None:
    ledger = CursorDispatchLedger.instance()
    row = _terminal_row(ledger, closeout_tokens=["ROW_HOP"])
    ledger.merge_record_json(
        dispatch_id="pred-hop-1",
        patch={"summoning_thread_id": "9638", "closeout_harvest_owed": True},
    )
    with ledger._connect() as conn:
        refreshed = conn.execute(
            "SELECT * FROM cursor_sdk_dispatches WHERE dispatch_id='pred-hop-1'"
        ).fetchone()
    row = {k: refreshed[k] for k in refreshed.keys()}
    with patch(
        "services.git_integration_worker.cursor_sdk_closeout.conductor_exit_reasons.read_external_gate_lane_snapshot",
        return_value=_live_gate_snap(),
    ):
        assert hop_owed(row, closeout_tokens=frozenset({"ROW_HOP"})) is False
        assert (
            _hop_skip_gate(row, closeout_tokens=frozenset({"ROW_HOP"}))
            == SKIP_GATE_LIVE_EXTERNAL
        )


def test_hop_owed_true_when_external_gate_completed_ac1() -> None:
    ledger = CursorDispatchLedger.instance()
    row = _terminal_row(ledger, closeout_tokens=["ROW_HOP"])
    ledger.merge_record_json(
        dispatch_id="pred-hop-1",
        patch={"summoning_thread_id": "9638"},
    )
    with ledger._connect() as conn:
        refreshed = conn.execute(
            "SELECT * FROM cursor_sdk_dispatches WHERE dispatch_id='pred-hop-1'"
        ).fetchone()
    row = {k: refreshed[k] for k in refreshed.keys()}
    with patch(
        "services.git_integration_worker.cursor_sdk_closeout.conductor_exit_reasons.read_external_gate_lane_snapshot",
        return_value={"observed_at": "2026-09-05T00:00:00+00:00", "rows": []},
    ):
        assert hop_owed(row, closeout_tokens=frozenset({"ROW_HOP"})) is True


@pytest.mark.asyncio
async def test_ac2_five_terminals_zero_posts_while_external_gate_live() -> None:
    ledger = CursorDispatchLedger.instance()
    dispatch_id = "pred-hop-1"
    _terminal_row(ledger, closeout_tokens=["ROW_HOP"], dispatch_id=dispatch_id)
    ledger.merge_record_json(
        dispatch_id=dispatch_id,
        patch={
            "summoning_thread_id": "9638",
            "closeout_harvest_owed": True,
        },
    )
    post_mock = AsyncMock(return_value=(True, {"dispatch_id": "should-not-fire"}))
    with patch(
        "services.git_integration_worker.cursor_sdk_closeout.conductor_exit_reasons.read_external_gate_lane_snapshot",
        return_value=_live_gate_snap(),
    ):
        with patch(
            "services.git_integration_worker.cursor_sdk_closeout.conductor_hop.post_conductor_hop_team_dispatch",
            post_mock,
        ):
            for _ in range(5):
                await maybe_fire_conductor_hop_reactor(dispatch_id=dispatch_id)
    post_mock.assert_not_called()


@pytest.mark.asyncio
async def test_live_external_gate_release_admits_without_waiting_grace() -> None:
    """A ROW_HOP deferred on a live CDP review admits once that stream ends.

    Reactor grace is 120s; the release wake must admit on the cleared snap
    with no sleep. While the snap is still live the wake posts nothing.
    """
    ledger = CursorDispatchLedger.instance()
    dispatch_id = "pred-hop-1"
    _terminal_row(
        ledger,
        closeout_tokens=["ROW_HOP"],
        dispatch_id=dispatch_id,
        summoning_thread_id="9638",
    )
    ledger.merge_record_json(
        dispatch_id=dispatch_id,
        patch={"summoning_thread_id": "9638", "closeout_harvest_owed": True},
    )
    state = {"snap": _live_gate_snap()}
    post_mock = AsyncMock(return_value=(True, {"dispatch_id": "succ-gate-release"}))
    released: list[dict] = []

    def _read_snap() -> dict:
        return state["snap"]

    with patch(
        "services.git_integration_worker.cursor_sdk_closeout.conductor_exit_reasons.read_external_gate_lane_snapshot",
        side_effect=_read_snap,
    ):
        with patch(
            "services.git_integration_worker.cursor_sdk_closeout.conductor_hop.post_conductor_hop_team_dispatch",
            post_mock,
        ):
            with patch(
                "services.git_integration_worker.cursor_sdk_closeout.conductor_hop.emit_frontier_sdk_conductor_hop_deferral_released",
                side_effect=lambda **kw: released.append(kw),
            ):
                await maybe_fire_conductor_hop_reactor(dispatch_id=dispatch_id)
                post_mock.assert_not_called()
                held = await release_deferred_conductor_hops()
                assert held == 0
                state["snap"] = {
                    "observed_at": "2026-09-05T00:00:01+00:00",
                    "rows": [
                        {
                            "execution_id": "exec-ext-gate",
                            "parent_thread": "9638",
                            "status": "completed",
                            "stream_state": "completed",
                            "purpose": "review",
                        }
                    ],
                }
                admitted = await release_deferred_conductor_hops()
    assert admitted == 1
    post_mock.assert_awaited()
    assert released
    assert released[0]["prior_gate"] == SKIP_GATE_LIVE_EXTERNAL
    assert released[0]["successor_dispatch_id"] == "succ-gate-release"
    assert released[0]["dispatch_id"] == dispatch_id


@pytest.mark.asyncio
async def test_next_admit_blocked_releases_when_harvest_execution_is_gone() -> None:
    """Specimen shape: NEXT_ADMIT harvest <id>, then the execution leaves the registry.

    While the id is absent and no deferral is stamped, the body stays refused.
    After the skip stamps next_admit_blocked, absence counts as terminal and
    the release wake admits. A permanent admit error does not retry.
    """
    ledger = CursorDispatchLedger.instance()
    dispatch_id = "pred-hop-1"
    harvest_id = "4858cfb2-c6e9-4157-ba43-f12f3584685d"
    _terminal_row(
        ledger,
        closeout_tokens=["ROW_HOP"],
        dispatch_id=dispatch_id,
        summoning_thread_id="9638",
    )
    ledger.merge_record_json(
        dispatch_id=dispatch_id,
        patch={
            "summoning_thread_id": "9638",
            "closeout_body": f"stop: ROW_HOP\nNEXT_ADMIT: harvest {harvest_id}\n",
            "closeout_stop_tokens": ["ROW_HOP"],
        },
    )
    clear = {"observed_at": "2026-10-03T07:59:00+00:00", "rows": []}
    post_mock = AsyncMock(return_value=(True, {"dispatch_id": "succ-harvest-gone"}))
    with patch(
        "services.git_integration_worker.cursor_sdk_closeout.conductor_exit_reasons.read_external_gate_lane_snapshot",
        return_value=clear,
    ):
        with patch(
            "services.git_integration_worker.cursor_sdk_closeout.conductor_hop.post_conductor_hop_team_dispatch",
            post_mock,
        ):
            await maybe_fire_conductor_hop_reactor(dispatch_id=dispatch_id)
            post_mock.assert_not_called()
            admitted = await release_deferred_conductor_hops()
    assert admitted == 1
    post_mock.assert_awaited()
    with ledger._connect() as conn:
        stored = conn.execute(
            "SELECT record_json FROM cursor_sdk_dispatches WHERE dispatch_id=?",
            (dispatch_id,),
        ).fetchone()
    record = json.loads(stored["record_json"])
    assert record.get("hop_deferral_gate") == SKIP_GATE_NEXT_ADMIT_BLOCKED


def _live_harvest_registry(harvest_id: str) -> dict:
    return {
        "live": {
            "execution_id": harvest_id,
            "execution_state": {
                "execution_id": harvest_id,
                "state": "streaming",
                "started_at": 1_700_000_000.0,
            },
        }
    }


@pytest.mark.asyncio
async def test_next_admit_blocked_releases_when_reply_on_summoning_thread() -> None:
    """Harvest row still live; web-anthropic reply on the summoning thread admits once."""
    ledger = CursorDispatchLedger.instance()
    dispatch_id = "pred-hop-reply"
    harvest_id = "4858cfb2-c6e9-4157-ba43-f12f3584685d"
    _terminal_row(
        ledger,
        closeout_tokens=["ROW_HOP"],
        dispatch_id=dispatch_id,
        summoning_thread_id="9638",
    )
    ledger.merge_record_json(
        dispatch_id=dispatch_id,
        patch={
            "summoning_thread_id": "9638",
            "closeout_turn": 9,
            "closeout_body": f"stop: ROW_HOP\nNEXT_ADMIT: harvest {harvest_id}\n",
            "closeout_stop_tokens": ["ROW_HOP"],
        },
    )
    post_mock = AsyncMock(return_value=(True, {"dispatch_id": "succ-reply-present"}))
    state = {"after_harvest": False}

    def _watermark(*, thread_id: str, closeout_instant: str) -> int | None:
        if thread_id != "9638" or not closeout_instant.startswith("2023-11-14"):
            return None
        return 7

    def _reply(thread_id: str, after_turn: int, from_agent: str) -> bool:
        return (
            state["after_harvest"]
            and thread_id == "9638"
            and after_turn == 7
            and from_agent == "web-anthropic"
        )

    with (
        patch(
            "claude_bundles.cdp_registry_store.load_active",
            return_value=_live_harvest_registry(harvest_id),
        ),
        patch(
            "services.git_integration_worker.cursor_sdk_closeout.conductor_park_harvest.resolve_consult_summoning_watermark_at_instant",
            side_effect=_watermark,
        ),
        patch(
            "services.git_integration_worker.cursor_sdk_closeout.conductor_park_harvest.reply_arrived_on_thread",
            side_effect=_reply,
        ),
        patch(
            "services.git_integration_worker.cursor_sdk_closeout.conductor_hop.post_conductor_hop_team_dispatch",
            post_mock,
        ),
    ):
        await maybe_fire_conductor_hop_reactor(dispatch_id=dispatch_id)
        post_mock.assert_not_called()
        held = await release_deferred_conductor_hops()
        assert held == 0
        post_mock.assert_not_called()
        state["after_harvest"] = True
        admitted = await release_deferred_conductor_hops()
        again = await release_deferred_conductor_hops()
    assert admitted == 1
    assert again == 0
    assert post_mock.await_count == 1


def test_harvest_target_token_accepts_backticks_and_trailing_prose() -> None:
    """14902 backticks and 14915 parenthetical must both yield the harvest id."""
    from services.git_integration_worker.cursor_sdk_closeout.conductor_hop import (
        _harvest_target_token,
    )

    backtick = (
        "NEXT_ADMIT: harvest `64b84918-f552-47a1-bf7e-ab634fcf9673`\nstop: ROW_HOP\n"
    )
    prose = (
        "NEXT_ADMIT: harvest 940600a3-fc7e-421c-bc63-5971620900bd "
        "(poll thread 14915 after_turn 57 from web-anthropic). Do not re-fire.\n"
        "stop: ROW_HOP\n"
    )
    assert _harvest_target_token(backtick) == "64b84918-f552-47a1-bf7e-ab634fcf9673"
    assert _harvest_target_token(prose) == "940600a3-fc7e-421c-bc63-5971620900bd"
    assert _harvest_target_token("NEXT_ADMIT: none\nstop: ROW_HOP\n") is None
    prior = (
        "NEXT_ADMIT: none — prior harvest "
        "64b84918-f552-47a1-bf7e-ab634fcf9673 already answered\n"
    )
    land = "NEXT_ADMIT: land after harvest 64b84918-f552-47a1-bf7e-ab634fcf9673\n"
    notes = "NEXT_ADMIT: see notes then harvest deadbeef\n"
    assert _harvest_target_token(prior) is None
    assert _harvest_target_token(land) is None
    assert _harvest_target_token(notes) is None
    dated = "NEXT_ADMIT: harvest 2026-10-03 results\nstop: ROW_HOP\n"
    two = (
        "NEXT_ADMIT: harvest 64b84918-f552-47a1-bf7e-ab634fcf9673 "
        "and harvest 940600a3-fc7e-421c-bc63-5971620900bd\n"
    )
    assert _harvest_target_token(dated) is None
    assert _harvest_target_token(two) is None


@pytest.mark.asyncio
async def test_row_hop_backtick_harvest_reply_admits_one_successor() -> None:
    """14902#90/#91: ROW_HOP, backtick harvest id, reply after closeout, one admit."""
    ledger = CursorDispatchLedger.instance()
    dispatch_id = "pred-hop-14902"
    harvest_id = "64b84918-f552-47a1-bf7e-ab634fcf9673"
    _terminal_row(
        ledger,
        closeout_tokens=["ROW_HOP"],
        dispatch_id=dispatch_id,
        summoning_thread_id="14902",
    )
    ledger.merge_record_json(
        dispatch_id=dispatch_id,
        patch={
            "summoning_thread_id": "14902",
            "closeout_turn": 90,
            "closeout_body": f"stop: ROW_HOP\nNEXT_ADMIT: harvest `{harvest_id}`\n",
            "closeout_stop_tokens": ["ROW_HOP"],
        },
    )
    post_mock = AsyncMock(return_value=(True, {"dispatch_id": "succ-14902"}))

    def _watermark(*, thread_id: str, closeout_instant: str) -> int | None:
        if thread_id != "14902" or not closeout_instant.startswith("2023-11-14"):
            return None
        return 90

    def _reply(thread_id: str, after_turn: int, from_agent: str) -> bool:
        return (
            thread_id == "14902" and after_turn == 90 and from_agent == "web-anthropic"
        )

    with (
        patch(
            "claude_bundles.cdp_registry_store.load_active",
            return_value=_live_harvest_registry(harvest_id),
        ),
        patch(
            "services.git_integration_worker.cursor_sdk_closeout.conductor_park_harvest.resolve_consult_summoning_watermark_at_instant",
            side_effect=_watermark,
        ),
        patch(
            "services.git_integration_worker.cursor_sdk_closeout.conductor_park_harvest.reply_arrived_on_thread",
            side_effect=_reply,
        ),
        patch(
            "services.git_integration_worker.cursor_sdk_closeout.conductor_hop.post_conductor_hop_team_dispatch",
            post_mock,
        ),
    ):
        await maybe_fire_conductor_hop_reactor(dispatch_id=dispatch_id)
        post_mock.assert_not_called()
        admitted = await release_deferred_conductor_hops()
        again = await release_deferred_conductor_hops()
    assert admitted == 1
    assert again == 0
    assert post_mock.await_count == 1
    with ledger._connect() as conn:
        stored = conn.execute(
            "SELECT record_json FROM cursor_sdk_dispatches WHERE dispatch_id=?",
            (dispatch_id,),
        ).fetchone()
    record = json.loads(stored["record_json"])
    assert record.get("hop_deferral_gate") == SKIP_GATE_NEXT_ADMIT_BLOCKED
    assert record.get("hop_successor") == "succ-14902"


@pytest.mark.asyncio
async def test_next_admit_blocked_releases_when_reply_precedes_closeout() -> None:
    """A reply before closeout_turn still clears next_admit_blocked once."""
    ledger = CursorDispatchLedger.instance()
    dispatch_id = "pred-hop-early-reply"
    harvest_id = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    _terminal_row(
        ledger,
        closeout_tokens=["ROW_HOP"],
        dispatch_id=dispatch_id,
        summoning_thread_id="14901",
    )
    ledger.merge_record_json(
        dispatch_id=dispatch_id,
        patch={
            "summoning_thread_id": "14901",
            "closeout_turn": 12,
            "closeout_body": f"stop: ROW_HOP\nNEXT_ADMIT: harvest {harvest_id}\n",
            "closeout_stop_tokens": ["ROW_HOP"],
        },
    )
    post_mock = AsyncMock(return_value=(True, {"dispatch_id": "succ-early-reply"}))
    state = {"reply": False}

    def _watermark(*, thread_id: str, closeout_instant: str) -> int | None:
        if not closeout_instant.startswith("2023-11-14"):
            return None
        return 2

    def _reply(thread_id: str, after_turn: int, from_agent: str) -> bool:
        # closeout_turn is 12. A reply after the harvest watermark (2) and
        # before that closeout must count. after_turn=0 must not.
        return state["reply"] and after_turn == 2 and from_agent == "web-anthropic"

    with (
        patch(
            "claude_bundles.cdp_registry_store.load_active",
            return_value=_live_harvest_registry(harvest_id),
        ),
        patch(
            "services.git_integration_worker.cursor_sdk_closeout.conductor_park_harvest.resolve_consult_summoning_watermark_at_instant",
            side_effect=_watermark,
        ),
        patch(
            "services.git_integration_worker.cursor_sdk_closeout.conductor_park_harvest.reply_arrived_on_thread",
            side_effect=_reply,
        ),
        patch(
            "services.git_integration_worker.cursor_sdk_closeout.conductor_hop.post_conductor_hop_team_dispatch",
            post_mock,
        ),
    ):
        await maybe_fire_conductor_hop_reactor(dispatch_id=dispatch_id)
        post_mock.assert_not_called()
        state["reply"] = True
        admitted = await release_deferred_conductor_hops()
        again = await release_deferred_conductor_hops()
    assert admitted == 1
    assert again == 0
    assert post_mock.await_count == 1


def _row_hop_harvest_terminal(
    ledger: CursorDispatchLedger,
    *,
    dispatch_id: str,
    thread_id: str,
    summoning_thread_id: str,
    harvest_id: str,
    closeout_turn: int,
    closeout_hop_seq: int,
    hop_seq: int,
) -> None:
    req = _req(
        dispatch_id=dispatch_id,
        thread_id=thread_id,
        execution_id=f"exec-{dispatch_id}",
    )
    _admit_conductor(ledger, req)
    ledger.merge_record_json(
        dispatch_id=dispatch_id,
        patch={
            "summoning_thread_id": summoning_thread_id,
            "closeout_turn": closeout_turn,
            "closeout_hop_seq": closeout_hop_seq,
            "hop_seq": hop_seq,
            "closeout_body": f"stop: ROW_HOP\nNEXT_ADMIT: harvest {harvest_id}\n",
            "closeout_stop_tokens": ["ROW_HOP"],
            "hop_deferral_gate": SKIP_GATE_NEXT_ADMIT_BLOCKED,
        },
    )
    ledger.mark_terminal(dispatch_id=dispatch_id, terminal_status="completed")


_CLEAR_GATE = {"observed_at": "2026-10-03T23:00:00+00:00", "rows": []}


@pytest.mark.asyncio
async def test_14915_reply_lifts_when_harvest_registry_watermark_missing() -> None:
    """a:37767: started_at missing; closeout_turn on the worker thread still lifts."""
    ledger = CursorDispatchLedger.instance()
    dispatch_id = "4d928ab0a8ef-14092bab"
    harvest_id = "d48cd73e-dbfd-4cdf-8ce9-7928cab578ab"
    _row_hop_harvest_terminal(
        ledger,
        dispatch_id=dispatch_id,
        thread_id="14915",
        summoning_thread_id="14915",
        harvest_id=harvest_id,
        closeout_turn=133,
        closeout_hop_seq=1,
        hop_seq=24,
    )
    post_mock = AsyncMock(return_value=(True, {"dispatch_id": "succ-14915"}))

    def _reply(thread_id: str, after_turn: int, from_agent: str) -> bool:
        return (
            thread_id == "14915"
            and after_turn == 133
            and from_agent == "web-anthropic"
        )

    with (
        patch(
            "services.git_integration_worker.cursor_sdk_closeout.conductor_hop._harvest_execution_started_at_iso",
            return_value=None,
        ),
        patch(
            "services.git_integration_worker.cursor_sdk_closeout.conductor_hop._named_target_is_terminal",
            return_value=False,
        ),
        patch(
            "services.git_integration_worker.cursor_sdk_closeout.conductor_park_harvest.reply_arrived_on_thread",
            side_effect=_reply,
        ),
        patch(
            "services.git_integration_worker.cursor_sdk_closeout.conductor_exit_reasons.read_external_gate_lane_snapshot",
            return_value=_CLEAR_GATE,
        ),
        patch(
            "services.git_integration_worker.cursor_sdk_closeout.conductor_hop.post_conductor_hop_team_dispatch",
            post_mock,
        ),
    ):
        admitted = await release_deferred_conductor_hops()
        again = await release_deferred_conductor_hops()
    assert admitted == 1
    assert again == 0
    assert post_mock.await_count == 1


@pytest.mark.asyncio
async def test_14901_registry_watermark_still_admits_once() -> None:
    """Control: aligned hop seq and a registry started_at still admit one successor."""
    ledger = CursorDispatchLedger.instance()
    dispatch_id = "1cda05999c22-5721985b"
    harvest_id = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    _row_hop_harvest_terminal(
        ledger,
        dispatch_id=dispatch_id,
        thread_id="14901",
        summoning_thread_id="14901",
        harvest_id=harvest_id,
        closeout_turn=144,
        closeout_hop_seq=28,
        hop_seq=28,
    )
    post_mock = AsyncMock(return_value=(True, {"dispatch_id": "succ-14901"}))

    def _watermark(*, thread_id: str, closeout_instant: str) -> int | None:
        if thread_id != "14901" or not closeout_instant.startswith("2023-11-14"):
            return None
        return 144

    def _reply(thread_id: str, after_turn: int, from_agent: str) -> bool:
        return (
            thread_id == "14901"
            and after_turn == 144
            and from_agent == "web-anthropic"
        )

    with (
        patch(
            "claude_bundles.cdp_registry_store.load_active",
            return_value=_live_harvest_registry(harvest_id),
        ),
        patch(
            "services.git_integration_worker.cursor_sdk_closeout.conductor_park_harvest.resolve_consult_summoning_watermark_at_instant",
            side_effect=_watermark,
        ),
        patch(
            "services.git_integration_worker.cursor_sdk_closeout.conductor_park_harvest.reply_arrived_on_thread",
            side_effect=_reply,
        ),
        patch(
            "services.git_integration_worker.cursor_sdk_closeout.conductor_exit_reasons.read_external_gate_lane_snapshot",
            return_value=_CLEAR_GATE,
        ),
        patch(
            "services.git_integration_worker.cursor_sdk_closeout.conductor_hop.post_conductor_hop_team_dispatch",
            post_mock,
        ),
    ):
        admitted = await release_deferred_conductor_hops()
        again = await release_deferred_conductor_hops()
    assert admitted == 1
    assert again == 0
    assert post_mock.await_count == 1


@pytest.mark.asyncio
async def test_busy_summoning_thread_closeout_turn_does_not_release() -> None:
    """closeout_turn is a worker-thread number; a busy summoning thread must not match."""
    ledger = CursorDispatchLedger.instance()
    dispatch_id = "4d928ab0-busy-summon"
    harvest_id = "d48cd73e-dbfd-4cdf-8ce9-7928cab578ab"
    _row_hop_harvest_terminal(
        ledger,
        dispatch_id=dispatch_id,
        thread_id="14915",
        summoning_thread_id="12286",
        harvest_id=harvest_id,
        closeout_turn=133,
        closeout_hop_seq=1,
        hop_seq=24,
    )
    post_mock = AsyncMock(return_value=(True, {"dispatch_id": "succ-false"}))

    def _reply(thread_id: str, after_turn: int, from_agent: str) -> bool:
        return thread_id == "12286" and from_agent == "web-anthropic"

    with (
        patch(
            "services.git_integration_worker.cursor_sdk_closeout.conductor_hop._harvest_execution_started_at_iso",
            return_value=None,
        ),
        patch(
            "services.git_integration_worker.cursor_sdk_closeout.conductor_hop._named_target_is_terminal",
            return_value=False,
        ),
        patch(
            "services.git_integration_worker.cursor_sdk_closeout.conductor_park_harvest.reply_arrived_on_thread",
            side_effect=_reply,
        ),
        patch(
            "services.git_integration_worker.cursor_sdk_closeout.conductor_exit_reasons.read_external_gate_lane_snapshot",
            return_value=_CLEAR_GATE,
        ),
        patch(
            "services.git_integration_worker.cursor_sdk_closeout.conductor_hop.post_conductor_hop_team_dispatch",
            post_mock,
        ),
    ):
        admitted = await release_deferred_conductor_hops()
    assert admitted == 0
    post_mock.assert_not_called()


def test_successor_hop_seq_uses_max_of_closeout_and_ledger() -> None:
    from services.git_integration_worker.cursor_sdk_closeout.conductor_hop import (
        _successor_hop_seq,
    )

    def _row(*, closeout: int | None, ledger_seq: int | None) -> tuple[dict, dict]:
        rec: dict[str, Any] = {}
        if closeout is not None:
            rec["closeout_hop_seq"] = closeout
        record: dict[str, Any] = {}
        if ledger_seq is not None:
            record["hop_seq"] = ledger_seq
        row = {"record_json": json.dumps(record)}
        return row, rec

    assert _successor_hop_seq(*_row(closeout=1, ledger_seq=24)) == 25
    assert _successor_hop_seq(*_row(closeout=28, ledger_seq=28)) == 29
    assert _successor_hop_seq(*_row(closeout=1, ledger_seq=None)) == 2
    assert _successor_hop_seq(*_row(closeout=None, ledger_seq=24)) == 25
    assert _successor_hop_seq(*_row(closeout=None, ledger_seq=None)) == 1


@pytest.mark.asyncio
async def test_ac8_probe_down_no_gate_owed_hop_proceeds() -> None:
    ledger = CursorDispatchLedger.instance()
    _terminal_row(ledger, closeout_tokens=["ROW_HOP"], summoning_thread_id="9638")
    ledger.merge_record_json(
        dispatch_id="pred-hop-1",
        patch={"closeout_harvest_owed": False},
    )
    with patch(
        "services.git_integration_worker.cursor_sdk_closeout.conductor_exit_reasons.read_external_gate_lane_snapshot",
        return_value={},
    ):
        with patch(
            "services.git_integration_worker.cursor_sdk_closeout.conductor_hop.post_conductor_hop_team_dispatch",
            AsyncMock(return_value=(True, {"dispatch_id": "succ-ac8-open"})),
        ):
            await maybe_fire_conductor_hop_reactor(dispatch_id="pred-hop-1")
    with ledger._connect() as conn:
        row = conn.execute(
            "SELECT record_json FROM cursor_sdk_dispatches WHERE dispatch_id='pred-hop-1'"
        ).fetchone()
    fields = hop_fields_from_record_json(row["record_json"])
    assert fields.get("hop_successor") == "succ-ac8-open"


@pytest.mark.asyncio
async def test_ac8_probe_down_gate_owed_no_hop() -> None:
    ledger = CursorDispatchLedger.instance()
    _terminal_row(ledger, closeout_tokens=["ROW_HOP"])
    ledger.merge_record_json(
        dispatch_id="pred-hop-1",
        patch={"closeout_harvest_owed": True},
    )
    post_mock = AsyncMock(return_value=(True, {"dispatch_id": "should-not-fire"}))
    skipped_gates: list[str] = []
    with patch(
        "services.git_integration_worker.cursor_sdk_closeout.conductor_exit_reasons.read_external_gate_lane_snapshot",
        return_value={},
    ):
        with patch(
            "services.git_integration_worker.cursor_sdk_closeout.conductor_hop.post_conductor_hop_team_dispatch",
            post_mock,
        ):
            with patch(
                "services.git_integration_worker.cursor_sdk_closeout.conductor_hop.emit_frontier_sdk_conductor_hop_skipped",
                side_effect=lambda **kw: skipped_gates.append(kw["gate"]),
            ):
                await maybe_fire_conductor_hop_reactor(dispatch_id="pred-hop-1")
    post_mock.assert_not_called()
    assert SKIP_GATE_PROBE_INDETERMINATE in skipped_gates


def test_ac7_build_hop_body_refused_when_next_admit_none_in_closeout() -> None:
    ledger = CursorDispatchLedger.instance()
    row = _terminal_row(ledger, closeout_tokens=["ROW_HOP"])
    ledger.merge_record_json(
        dispatch_id="pred-hop-1",
        patch={"closeout_body": _NONE_NEXT_ADMIT_CLOSEOUT},
    )
    with ledger._connect() as conn:
        refreshed = conn.execute(
            "SELECT * FROM cursor_sdk_dispatches WHERE dispatch_id='pred-hop-1'"
        ).fetchone()
    row = {k: refreshed[k] for k in refreshed.keys()}
    assert build_hop_team_dispatch_body(row) is None


def test_ac7_build_hop_body_refused_when_scoreboard_next_admit_land(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CORTEX_FILES_ROOT", str(tmp_path))
    scoreboards = tmp_path / "notes/system/scoreboards"
    scoreboards.mkdir(parents=True)
    body = (
        "# Scoreboard\n\n"
        "- **NEXT_ADMIT:** git_land · sidecar L1\n\n"
        "| G8 | Land | OPEN |\n"
    )
    (scoreboards / "conductor-hop-fixture-scoreboard.md").write_text(
        body, encoding="utf-8"
    )
    ledger = CursorDispatchLedger.instance()
    row = _terminal_row(ledger, closeout_tokens=["ROW_HOP"])
    assert build_hop_team_dispatch_body(row) is None


def test_ac7_build_hop_body_refused_when_fold_gate_not_in_tip_table_prose_only(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Synthetic G_ROWS fold gate must not beat a tip table row (G1 prose only)."""
    monkeypatch.setenv("CORTEX_FILES_ROOT", str(tmp_path))
    scoreboards = tmp_path / "notes/system/scoreboards"
    scoreboards.mkdir(parents=True)
    body = (
        "# Scoreboard\n\n"
        "Resume at G1 when the ladder opens.\n\n"
        "- **NEXT_ADMIT:** git_land · sidecar L1\n\n"
        "| G8 | Land | OPEN |\n"
    )
    (scoreboards / "conductor-hop-fixture-scoreboard.md").write_text(
        body, encoding="utf-8"
    )
    ledger = CursorDispatchLedger.instance()
    row = _terminal_row(ledger, closeout_tokens=["ROW_HOP"])
    assert build_hop_team_dispatch_body(row) is None


def test_ac7_none_fold_entry_gate_does_not_fall_through_to_tip_table(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When the fold yields no entry gate, do not substitute the first OPEN tip row."""
    from services.git_integration_worker.cursor_sdk_closeout.conductor_hop import (
        _live_entry_gate_for_row,
    )

    monkeypatch.setenv("CORTEX_FILES_ROOT", str(tmp_path))
    scoreboards = tmp_path / "notes/system/scoreboards"
    scoreboards.mkdir(parents=True)
    body = "# Scoreboard\n\n- **NEXT_ADMIT:** harvest G1\n\n| G8 | Land | OPEN |\n"
    (scoreboards / "conductor-hop-fixture-scoreboard.md").write_text(
        body, encoding="utf-8"
    )
    ledger = CursorDispatchLedger.instance()
    row = _terminal_row(ledger, closeout_tokens=["ROW_HOP"])
    fold_stub = object()
    with patch(
        "implement_admission.conductor_witness.fold_scoreboard",
        return_value=fold_stub,
    ):
        with patch(
            "implement_admission.conductor_witness.resolve_entry_gate_from_fold",
            return_value=None,
        ):
            assert _live_entry_gate_for_row(row, body) is None


def test_ac7_scoreboard_land_admit_keeps_fold_gate_when_gate_in_tip_table(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Real gated-deliverables rows keep the fold entry gate (land admit ≠ that gate)."""

    from implement_admission.conductor_witness import (
        FoldDeps,
        fold_scoreboard,
        resolve_entry_gate_from_fold,
    )

    class _EmptyWitnessCortex:
        """Hermetic G1 reader — never opens HOME-relative cortex.db (a:37150)."""

        def entity_get(self, entity_id: str, **kwargs: Any) -> dict[str, Any]:
            _ = kwargs
            return {"id": entity_id, "attributes": {}}

        def list_relationships(
            self,
            entity_id: str,
            *,
            type_id: str | None = None,
        ) -> list[dict[str, Any]]:
            _ = entity_id, type_id
            return []

    def _boom_cortex_conn(*_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError(
            "cortex_conn opened — AC7 fold must stay hermetic (a:37150)"
        )

    # A1 (14141 review): fail if any path opens the production cortex DB.
    monkeypatch.setattr("cortex_store.db.cortex_conn", _boom_cortex_conn)

    stub_deps = FoldDeps(
        cortex=_EmptyWitnessCortex(),
        source_ref=_WORK_KEY,
        repo=tmp_path / "repo",
    )

    def _stub_fold_deps(
        source_ref: str,
        *,
        repo: Path,
        summon_mode: str | None = None,
        summoning_thread_id: str | None = None,
    ) -> FoldDeps:
        _ = source_ref, repo, summon_mode, summoning_thread_id
        return stub_deps

    # A2 (14141 review): hop-body live fold uses the same stub, not DefaultWitnessCortex.
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_sdk_nested_witness.fold_deps_with_ledger",
        _stub_fold_deps,
    )

    monkeypatch.setenv("CORTEX_FILES_ROOT", str(tmp_path))
    scoreboards = tmp_path / "notes/system/scoreboards"
    scoreboards.mkdir(parents=True)
    body = (
        "# Scoreboard\n\n"
        "- **NEXT_ADMIT:** git_land\n\n"
        "## Gated deliverables\n\n"
        "| ID | Deliverable | Status |\n|---|---|---|\n"
        "| G1 | Architecture | OPEN |\n"
        "| G2 | Frame | OPEN |\n"
        "| G3 | Plan | OPEN |\n"
        "| G4 | Skeptic | OPEN |\n"
        "| G5 | Implement | OPEN |\n"
        "| G6 | Review | OPEN |\n"
        "| G7 | Verify | OPEN |\n"
    )
    (scoreboards / "conductor-hop-fixture-scoreboard.md").write_text(
        body, encoding="utf-8"
    )
    fold = fold_scoreboard(
        "conductor-hop-fixture",
        deps=stub_deps,
        write_journal=False,
    )
    assert fold is not None
    assert resolve_entry_gate_from_fold(fold) == "G1"
    ledger = CursorDispatchLedger.instance()
    row = _terminal_row(ledger, closeout_tokens=["ROW_HOP"])
    assert build_hop_team_dispatch_body(row) is not None


def test_ac7_stale_header_entry_gate_fold_wins_over_harvest_next_admit(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Stale **Entry gate:** header must not outrank fold for NEXT_ADMIT guard (F1)."""
    from claude_bundles.conductor_stop import next_admit_payload_matches_entry_gate

    from services.git_integration_worker.cursor_sdk_closeout.conductor_hop import (
        _live_entry_gate_for_row,
    )

    monkeypatch.setenv("CORTEX_FILES_ROOT", str(tmp_path))
    scoreboards = tmp_path / "notes/system/scoreboards"
    scoreboards.mkdir(parents=True)
    body = (
        "# Scoreboard\n\n"
        "- **Entry gate:** G1\n"
        "- **NEXT_ADMIT:** harvest G1\n\n"
        "## Gated deliverables\n\n"
        "| ID | Deliverable | Status |\n|---|---|---|\n"
        "| G1 | Architecture | DONE |\n"
        "| G2 | Frame | OPEN |\n"
    )
    (scoreboards / "conductor-hop-fixture-scoreboard.md").write_text(
        body, encoding="utf-8"
    )
    ledger = CursorDispatchLedger.instance()
    row = _terminal_row(ledger, closeout_tokens=["ROW_HOP"])
    fold_stub = object()
    with patch(
        "implement_admission.conductor_witness.fold_scoreboard",
        return_value=fold_stub,
    ):
        with patch(
            "implement_admission.conductor_witness.resolve_entry_gate_from_fold",
            return_value="G2",
        ):
            assert _live_entry_gate_for_row(row, body) == "G2"
            assert not next_admit_payload_matches_entry_gate("harvest G1", "G2")
            body_out = build_hop_team_dispatch_body(row)
    assert body_out is not None
    assert body_out["generation_options"]["scoreboard_entry_gate"] == "G1"


def test_ac7_stale_scoreboard_harvest_does_not_block_row_hop(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Stale harvest NEXT_ADMIT on a prior gate must not wedge ROW_HOP successors."""
    monkeypatch.setenv("CORTEX_FILES_ROOT", str(tmp_path))
    scoreboards = tmp_path / "notes/system/scoreboards"
    scoreboards.mkdir(parents=True)
    body = (
        "# Scoreboard\n\n"
        "- **Entry gate:** G4\n"
        "- **NEXT_ADMIT:** harvest G1\n"
        "- **NEXT_ADMIT:** Conductor hop 3 — entry gate G4\n\n"
        "| G4 | Skeptic | OPEN |\n"
        "| G8 | Land | DONE |\n"
    )
    (scoreboards / "conductor-hop-fixture-scoreboard.md").write_text(
        body, encoding="utf-8"
    )
    ledger = CursorDispatchLedger.instance()
    row = _terminal_row(ledger, closeout_tokens=["ROW_HOP"])
    assert build_hop_team_dispatch_body(row) is not None


def test_r_row_entry_gate_sets_generation_option(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """D4: R-row entry gates use the scoreboard row-id grammar, not G[1-8]."""
    assert _scoreboard_entry_gate("**Entry gate:** R4") == "R4"
    monkeypatch.setenv("CORTEX_FILES_ROOT", str(tmp_path))
    scoreboards = tmp_path / "notes/system/scoreboards"
    scoreboards.mkdir(parents=True)
    (scoreboards / "conductor-hop-fixture-scoreboard.md").write_text(
        "# Scoreboard\n\n**Entry gate:** R4\n\n| R4 | Review | OPEN |\n",
        encoding="utf-8",
    )
    ledger = CursorDispatchLedger.instance()
    row = _terminal_row(ledger, closeout_tokens=["ROW_HOP"])
    body = build_hop_team_dispatch_body(row)
    assert body is not None
    assert body["generation_options"]["scoreboard_entry_gate"] == "R4"


@pytest.mark.asyncio
async def test_ac7_reactor_skips_with_next_admit_blocked_gate() -> None:
    ledger = CursorDispatchLedger.instance()
    _terminal_row(ledger, closeout_tokens=["ROW_HOP"])
    ledger.merge_record_json(
        dispatch_id="pred-hop-1",
        patch={"closeout_body": _NONE_NEXT_ADMIT_CLOSEOUT},
    )
    post_mock = AsyncMock(return_value=(True, {"dispatch_id": "should-not-fire"}))
    skipped_gates: list[str] = []
    with patch(
        "services.git_integration_worker.cursor_sdk_closeout.conductor_hop.post_conductor_hop_team_dispatch",
        post_mock,
    ):
        with patch(
            "services.git_integration_worker.cursor_sdk_closeout.conductor_hop.emit_frontier_sdk_conductor_hop_skipped",
            side_effect=lambda **kw: skipped_gates.append(kw["gate"]),
        ):
            await maybe_fire_conductor_hop_reactor(dispatch_id="pred-hop-1")
    post_mock.assert_not_called()
    assert SKIP_GATE_NEXT_ADMIT_BLOCKED in skipped_gates


_FULL_ID = "a1b2c3d4-e5f6-7890-abcd-ef1234567890"
_PREFIX = "a1b2c3d4"
_ZERO_ID = "ffffffffffffffff"
_LIVE_ID = "1111111111111111"
_SATELLITE = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
_TRUTHY_SNAP = {"observed_at": "2026-10-02T00:00:00+00:00", "rows": []}
_FINISHED_REGISTRY = {
    "reg-1": {
        "execution_id": _SATELLITE,
        "execution_state": {"execution_id": _SATELLITE, "state": "finished"},
    }
}


def _g2_harvest_closeout(harvest_id: str) -> str:
    return f"status: complete\nstop: ROW_HOP\nNEXT_ADMIT: harvest {harvest_id}\n"


def _stamp_g2_row(
    ledger: CursorDispatchLedger,
    *,
    harvest_id: str = _FULL_ID,
    closeout_tokens: list[str] | None = None,
    summoning_thread_id: str = "9638",
) -> dict:
    tokens = closeout_tokens if closeout_tokens is not None else ["ROW_HOP"]
    _terminal_row(
        ledger,
        closeout_tokens=tokens,
        summoning_thread_id=summoning_thread_id,
    )
    ledger.merge_record_json(
        dispatch_id="pred-hop-1",
        patch={
            "closeout_body": _g2_harvest_closeout(harvest_id),
            "closeout_stop_tokens": tokens,
            "summoning_thread_id": summoning_thread_id,
        },
    )
    return _refresh_row(ledger, "pred-hop-1")


def _create_inflight_db(tmp_path: Path, execution_id: str, satellite: str) -> None:
    db_path = tmp_path / "stargate-cdp-generate-inflight.db"
    conn = sqlite3.connect(db_path)
    conn.execute(
        "CREATE TABLE cdp_inflight_leg "
        "(execution_id TEXT PRIMARY KEY, satellite_execution_id TEXT)"
    )
    conn.execute(
        "INSERT INTO cdp_inflight_leg (execution_id, satellite_execution_id) "
        "VALUES (?, ?)",
        (execution_id, satellite),
    )
    conn.commit()
    conn.close()


def _admit_nested_dispatch(
    ledger: CursorDispatchLedger,
    *,
    dispatch_id: str,
    nest_under: str,
    status: str = "completed",
    thread_id: str = "9965",
    work_key: str | None = None,
) -> None:
    req = _req(dispatch_id=dispatch_id, thread_id=thread_id)
    wk = work_key or _WORK_KEY
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
        contract="implement",
        source_repo="/repo",
        lease_key="/repo",
        work_key=wk,
        source_ref=wk,
    )
    ledger.merge_record_json(
        dispatch_id=dispatch_id,
        patch={"contract": "implement", "lane": "B"},
    )
    ledger.merge_record_json(
        dispatch_id=dispatch_id,
        patch={"nest_under": nest_under},
    )
    if status in ("queued", "admitted", "running", "parked_waiting"):
        with ledger._connect() as conn:
            conn.execute(
                "UPDATE cursor_sdk_dispatches SET status=? WHERE dispatch_id=?",
                (status, dispatch_id),
            )
    else:
        ledger.mark_terminal(dispatch_id=dispatch_id, terminal_status=status)


def test_g2_named_id_still_live_body_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "claude_bundles.cdp_registry_store.load_active",
        lambda: {},
    )
    ledger = CursorDispatchLedger.instance()
    row = _stamp_g2_row(ledger, harvest_id=_LIVE_ID)
    with ledger._connect() as conn:
        conn.execute(
            "INSERT INTO cursor_sdk_dispatches "
            "(dispatch_id, thread_id, status, fingerprint, execution_id, "
            "caller_agent, resolved_model, contract, source_repo, lease_key, "
            "work_key, source_ref, record_json) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                _LIVE_ID,
                "9964",
                "running",
                "fp-live",
                "exec-live",
                "cursor",
                "composer-2.5",
                "implement",
                "/repo",
                "/repo",
                _WORK_KEY,
                _WORK_KEY,
                "{}",
            ),
        )
    assert build_hop_team_dispatch_body(row) is None

    for state in ("seated", "streaming", "awaiting_wake", "transferred", "expired"):
        monkeypatch.setattr(
            "claude_bundles.cdp_registry_store.load_active",
            lambda _s=state: {
                "reg-live": {
                    "execution_id": _FULL_ID,
                    "execution_state": {
                        "execution_id": _FULL_ID,
                        "state": _s,
                    },
                }
            },
        )
        row_exec = _stamp_g2_row(ledger, harvest_id=_FULL_ID)
        assert build_hop_team_dispatch_body(row_exec) is None


def test_g2_prefix_matches_zero_or_many_body_none(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ledger = CursorDispatchLedger.instance()
    monkeypatch.setattr(
        "claude_bundles.cdp_registry_store.load_active",
        lambda: {},
    )
    row_zero = _stamp_g2_row(ledger, harvest_id=_ZERO_ID)
    assert build_hop_team_dispatch_body(row_zero) is None

    ledger2 = CursorDispatchLedger.instance()
    _stamp_g2_row(ledger2, harvest_id=_PREFIX)
    with ledger2._connect() as conn:
        for suffix in ("aaaaaaaa", "bbbbbbbb"):
            conn.execute(
                "INSERT INTO cursor_sdk_dispatches "
                "(dispatch_id, thread_id, status, fingerprint, execution_id, "
                "caller_agent, resolved_model, contract, source_repo, lease_key, "
                "work_key, source_ref, record_json) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    f"{_PREFIX}{suffix}",
                    "9964",
                    "completed",
                    f"fp-{suffix}",
                    f"exec-{suffix}",
                    "cursor",
                    "composer-2.5",
                    "implement",
                    "/repo",
                    "/repo",
                    _WORK_KEY,
                    _WORK_KEY,
                    "{}",
                ),
            )
    row_many = _refresh_row(ledger2, "pred-hop-1")
    assert build_hop_team_dispatch_body(row_many) is None

    def _raise_load_active() -> dict:
        raise RuntimeError("registry down")

    ledger3 = CursorDispatchLedger.instance()
    _stamp_g2_row(ledger3, harvest_id=_FULL_ID)
    _admit_nested_dispatch(
        ledger3, dispatch_id=_FULL_ID, nest_under="pred-hop-1", status="completed"
    )
    row_term = _refresh_row(ledger3, "pred-hop-1")
    monkeypatch.setattr(
        "claude_bundles.cdp_registry_store.load_active",
        _raise_load_active,
    )
    assert build_hop_team_dispatch_body(row_term) is None

    bad_path = tmp_path / "stargate-cdp-generate-inflight.db"
    bad_path.write_text("not a sqlite database", encoding="utf-8")
    ledger4 = CursorDispatchLedger.instance()
    row_bad = _stamp_g2_row(ledger4, harvest_id=_FULL_ID)
    assert build_hop_team_dispatch_body(row_bad) is None


def test_g2_terminal_id_other_nest_live_no_post(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "claude_bundles.cdp_registry_store.load_active",
        lambda: {},
    )
    ledger = CursorDispatchLedger.instance()
    _stamp_g2_row(ledger, harvest_id=_FULL_ID)
    _admit_nested_dispatch(
        ledger,
        dispatch_id=_FULL_ID,
        nest_under="pred-hop-1",
        status="completed",
    )
    _admit_nested_dispatch(
        ledger,
        dispatch_id="live-nest-1",
        nest_under="pred-hop-1",
        status="running",
        work_key="todo:nest-live-nest-1",
    )
    row = _refresh_row(ledger, "pred-hop-1")
    assert hop_owed(row, closeout_tokens=frozenset({"ROW_HOP"})) is False
    skip = _hop_skip_gate(row, closeout_tokens=frozenset({"ROW_HOP"}))
    assert skip == "live_nested"
    assert build_hop_team_dispatch_body(row) is None


@pytest.mark.asyncio
async def test_g2_watchdog_admits_once_execution_terminal_past_grace(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _create_inflight_db(tmp_path, _FULL_ID, _SATELLITE)
    ledger = CursorDispatchLedger.instance()
    grace = load_hop_budget_config().reactor_grace_s
    terminal_at = time.time() - (grace + 30)
    _stamp_g2_row(ledger)
    ledger.merge_record_json(
        dispatch_id="pred-hop-1",
        patch={"hop_last_terminal_at": terminal_at},
    )
    with ledger._connect() as conn:
        conn.execute(
            "UPDATE cursor_sdk_dispatches SET terminal_at=? WHERE dispatch_id=?",
            (terminal_at, "pred-hop-1"),
        )
    monkeypatch.setattr(
        "claude_bundles.cdp_registry_store.load_active",
        lambda: _FINISHED_REGISTRY,
    )
    post = AsyncMock(return_value=(True, {"dispatch_id": "succ-g2-watch"}))
    with (
        patch(
            "services.git_integration_worker.cursor_sdk_closeout.conductor_exit_reasons.read_external_gate_lane_snapshot",
            return_value=_TRUTHY_SNAP,
        ),
        patch(
            "services.git_integration_worker.cursor_sdk_closeout.conductor_exit_reasons.cdp_ask_health_red",
            return_value=False,
        ),
        patch(
            "services.git_integration_worker.cursor_sdk_closeout.conductor_hop_watchdog.post_conductor_hop_team_dispatch",
            post,
        ),
    ):
        first = await maybe_fire_conductor_hop_watchdog(dispatch_id="pred-hop-1")
        second = await maybe_fire_conductor_hop_watchdog(dispatch_id="pred-hop-1")
    assert first is True
    assert second is False
    assert post.await_count == 1
    assert post.await_args.args[0]["hop_reason"] == "watchdog"
    data = json.loads(_refresh_row(ledger, "pred-hop-1")["record_json"])
    assert data.get("hop_successor") == "succ-g2-watch"


@pytest.mark.asyncio
async def test_g2_reactor_admits_once_nest_terminal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ledger = CursorDispatchLedger.instance()
    _admit_nested_dispatch(
        ledger,
        dispatch_id=_FULL_ID,
        nest_under="pred-hop-1",
        status="completed",
    )
    _stamp_g2_row(ledger)
    monkeypatch.setattr(
        "claude_bundles.cdp_registry_store.load_active",
        lambda: {},
    )
    row = _refresh_row(ledger, "pred-hop-1")
    with patch(
        "services.git_integration_worker.cursor_sdk_closeout.conductor_exit_reasons.read_external_gate_lane_snapshot",
        return_value={},
    ):
        assert build_hop_team_dispatch_body(row) is not None
    post = AsyncMock(return_value=(True, {"dispatch_id": "succ-g2-nest"}))
    with (
        patch(
            "services.git_integration_worker.cursor_sdk_closeout.conductor_exit_reasons.read_external_gate_lane_snapshot",
            return_value=_TRUTHY_SNAP,
        ),
        patch(
            "services.git_integration_worker.cursor_sdk_closeout.conductor_hop.post_conductor_hop_team_dispatch",
            post,
        ),
    ):
        await maybe_fire_conductor_hop_reactor(dispatch_id="pred-hop-1")
    assert post.await_count == 1
    assert post.await_args.args[0]["hop_reason"] == "planned"
    data = json.loads(_refresh_row(ledger, "pred-hop-1")["record_json"])
    assert data.get("hop_successor") == "succ-g2-nest"


@pytest.mark.asyncio
async def test_g2_reactor_and_watchdog_one_successor(monkeypatch) -> None:
    ledger = CursorDispatchLedger.instance()
    _admit_nested_dispatch(
        ledger,
        dispatch_id=_FULL_ID,
        nest_under="pred-hop-1",
        status="completed",
    )
    _stamp_g2_row(ledger)
    monkeypatch.setattr(
        "claude_bundles.cdp_registry_store.load_active",
        lambda: {},
    )
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {"dispatch_id": "succ-g2-race"}
    client = MagicMock()
    client.post = AsyncMock(return_value=resp)

    async def _enter(*_a, **_k):
        return client

    async def _exit(*_a, **_k):
        return None

    holder = MagicMock()
    holder.__aenter__ = _enter
    holder.__aexit__ = _exit
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_sdk_closeout.conductor_hop.make_async_client",
        lambda *_a, **_k: holder,
    )
    with patch(
        "services.git_integration_worker.cursor_sdk_closeout.conductor_exit_reasons.read_external_gate_lane_snapshot",
        return_value=_TRUTHY_SNAP,
    ):
        await maybe_fire_conductor_hop_reactor(dispatch_id="pred-hop-1")
    data = json.loads(_refresh_row(ledger, "pred-hop-1")["record_json"])
    assert data.get("hop_successor") == "succ-g2-race"
    row = _refresh_row(ledger, "pred-hop-1")
    watchdog_body = build_hop_team_dispatch_body(row, hop_reason_override="watchdog")
    assert watchdog_body is not None
    ok, detail = await post_conductor_hop_team_dispatch(watchdog_body)
    assert ok is False
    assert detail.get("reason") == "stop_not_claimed"
    assert client.post.await_count == 1
    fired = await maybe_fire_conductor_hop_watchdog(dispatch_id="pred-hop-1")
    assert fired is False
    data = json.loads(_refresh_row(ledger, "pred-hop-1")["record_json"])
    assert data.get("hop_successor") == "succ-g2-race"


def test_g2_empty_snap_or_health_red_not_terminal(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _create_inflight_db(tmp_path, _FULL_ID, _SATELLITE)
    monkeypatch.setattr(
        "claude_bundles.cdp_registry_store.load_active",
        lambda: _FINISHED_REGISTRY,
    )
    ledger = CursorDispatchLedger.instance()
    row = _stamp_g2_row(ledger)
    with (
        patch(
            "services.git_integration_worker.cursor_sdk_closeout.conductor_exit_reasons.read_external_gate_lane_snapshot",
            return_value={},
        ),
        patch(
            "services.git_integration_worker.cursor_sdk_closeout.conductor_exit_reasons.cdp_ask_health_red",
            return_value=False,
        ),
    ):
        assert build_hop_team_dispatch_body(row) is None
    with (
        patch(
            "services.git_integration_worker.cursor_sdk_closeout.conductor_exit_reasons.read_external_gate_lane_snapshot",
            return_value=_TRUTHY_SNAP,
        ),
        patch(
            "services.git_integration_worker.cursor_sdk_closeout.conductor_exit_reasons.cdp_ask_health_red",
            return_value=True,
        ),
    ):
        assert build_hop_team_dispatch_body(row) is None


def test_g2_harvest_g1_row_hop_body_none() -> None:
    ledger = CursorDispatchLedger.instance()
    row = _stamp_g2_row(ledger, harvest_id="G1")
    assert build_hop_team_dispatch_body(row) is None


def test_g2_parked_transport_terminal_harvest_body_none() -> None:
    ledger = CursorDispatchLedger.instance()
    row = _stamp_g2_row(ledger, closeout_tokens=["PARKED_TRANSPORT"])
    ledger.merge_record_json(
        dispatch_id="pred-hop-1",
        patch={"closeout_turn": 48},
    )
    _admit_nested_dispatch(
        ledger,
        dispatch_id=_FULL_ID,
        nest_under="pred-hop-1",
        status="completed",
    )
    row = _refresh_row(ledger, "pred-hop-1")
    assert build_hop_team_dispatch_body(row) is None
    assert not park_harvest_continue_owed(row, reply_fn=lambda *_a, **_k: False)


def test_ac10_three_hop_post_paths_use_shared_builder() -> None:
    import inspect

    from services.git_integration_worker.cursor_sdk_closeout import (
        conductor_hop_watchdog,
        conductor_park_harvest,
    )

    assert (
        conductor_hop_watchdog.build_hop_team_dispatch_body
        is build_hop_team_dispatch_body
    )
    park_source = inspect.getsource(conductor_park_harvest.fire_park_harvest_continue)
    assert (
        "services.git_integration_worker.cursor_sdk_closeout.conductor_hop"
        in park_source
    )
    assert "build_hop_team_dispatch_body" in park_source


def _refresh_row(ledger: CursorDispatchLedger, dispatch_id: str) -> dict:
    with ledger._connect() as conn:
        row = conn.execute(
            "SELECT * FROM cursor_sdk_dispatches WHERE dispatch_id=?",
            (dispatch_id,),
        ).fetchone()
    return {k: row[k] for k in row.keys()}


@pytest.mark.parametrize(
    "hop_reason_override, expected_reason",
    [
        (None, "planned"),
        ("watchdog", "watchdog"),
        ("park_harvest", "park_harvest"),
        ("producer_harvest", "producer_harvest"),
    ],
)
def test_ac10_hop_body_conforms_to_team_dispatch_generate(
    hop_reason_override: str | None,
    expected_reason: str,
) -> None:
    """AC-W1 — shared hop builder validates on Stargate generate wire."""
    from systems.frontier_consult.route import TeamDispatchGenerateBody

    ledger = CursorDispatchLedger.instance()
    _terminal_row(ledger, closeout_tokens=["ROW_HOP"])
    ledger.merge_record_json(
        dispatch_id="pred-hop-1",
        patch={"summoning_thread_id": "10223"},
    )
    row = _refresh_row(ledger, "pred-hop-1")
    body = build_hop_team_dispatch_body(row, hop_reason_override=hop_reason_override)
    assert body is not None
    assert "packet_kind" not in body
    parsed = TeamDispatchGenerateBody(**body)
    assert parsed.job == "conductor"
    assert parsed.hop_reason == expected_reason
    assert parsed.hop_from == "pred-hop-1"
    assert parsed.hop_seq == 2
    assert parsed.dispatch_thread_id == "10223"


# --- AC-B1–B6 (park-after-skip P2) ---


def _crash_chain(ledger: CursorDispatchLedger, *, count: int = 3) -> str:
    """Admit *count* consecutive failed crash rows; return last dispatch_id."""
    last_id = ""
    for idx in range(1, count + 1):
        last_id = f"ac-b-crash-{idx}"
        _admit_conductor(
            ledger,
            _req(dispatch_id=last_id),
            record_patch={"hop_entry_gate": "G4", "hop_witnessed_done": []},
        )
        ledger.mark_terminal(dispatch_id=last_id, terminal_status="failed")
    return last_id


@pytest.mark.asyncio
async def test_ac_b1_done_after_crash_chain_no_park_announce() -> None:
    """AC-B1: three failed priors + DONE current → skipped, no park post."""
    ledger = CursorDispatchLedger.instance()
    _crash_chain(ledger, count=3)
    dispatch_id = "ac-b1-done"
    _admit_conductor(ledger, _req(dispatch_id=dispatch_id))
    ledger.merge_record_json(
        dispatch_id=dispatch_id,
        patch={"closeout_stop_tokens": ["DONE"], "hop_entry_gate": "G4"},
    )
    ledger.mark_terminal(dispatch_id=dispatch_id, terminal_status="completed")
    skipped_gates: list[str] = []
    with (
        patch(
            "services.git_integration_worker.cursor_sdk_closeout.conductor_hop_park.default_park_poster",
        ) as park_poster,
        patch(
            "services.git_integration_worker.cursor_sdk_closeout.conductor_hop_park.page_hop_budget_parked",
            AsyncMock(return_value=True),
        ),
        patch(
            "services.git_integration_worker.cursor_sdk_closeout.conductor_hop.post_conductor_hop_team_dispatch",
            AsyncMock(),
        ) as post_mock,
        patch(
            "services.git_integration_worker.cursor_sdk_closeout.conductor_hop.emit_frontier_sdk_conductor_hop_skipped",
            side_effect=lambda **kw: skipped_gates.append(kw["gate"]),
        ),
    ):
        await maybe_fire_conductor_hop_reactor(dispatch_id=dispatch_id)
    park_poster.assert_not_called()
    post_mock.assert_not_called()
    assert "mission_closed" in skipped_gates
    with ledger._connect() as conn:
        row = conn.execute(
            "SELECT record_json FROM cursor_sdk_dispatches WHERE dispatch_id=?",
            (dispatch_id,),
        ).fetchone()
    data = json.loads(row["record_json"])
    assert data.get("hop_parked") is not True


@pytest.mark.asyncio
async def test_ac_b2_mission_cap_done_current_no_announce() -> None:
    """AC-B2: mission_cap=3 exhausted + DONE current → no park announcement."""
    ledger = CursorDispatchLedger.instance()
    for idx in range(1, 4):
        req = _req(dispatch_id=f"ac-b2-{idx}")
        _admit_conductor(
            ledger,
            req,
            record_patch={
                "closeout_stop_tokens": ["ROW_HOP"],
                "hop_entry_gate": "G4",
                "hop_witnessed_done": [],
            },
        )
        ledger.mark_terminal(dispatch_id=f"ac-b2-{idx}", terminal_status="completed")
    dispatch_id = "ac-b2-done"
    _admit_conductor(ledger, _req(dispatch_id=dispatch_id))
    ledger.merge_record_json(
        dispatch_id=dispatch_id,
        patch={"closeout_stop_tokens": ["DONE"], "hop_entry_gate": "G4"},
    )
    ledger.mark_terminal(dispatch_id=dispatch_id, terminal_status="completed")
    with (
        patch(
            "services.git_integration_worker.cursor_sdk_closeout.conductor_hop_budget.load_hop_budget_config",
            return_value=HopBudgetConfig(
                crash_cap_per_row=3,
                no_progress_cap=2,
                mission_cap=3,
                crash_backoff_s=(30.0, 120.0, 300.0),
                reactor_grace_s=120.0,
            ),
        ),
        patch(
            "services.git_integration_worker.cursor_sdk_closeout.conductor_hop_park.default_park_poster",
        ) as park_poster,
        patch(
            "services.git_integration_worker.cursor_sdk_closeout.conductor_hop.post_conductor_hop_team_dispatch",
            AsyncMock(),
        ),
    ):
        await maybe_fire_conductor_hop_reactor(dispatch_id=dispatch_id)
    park_poster.assert_not_called()


@pytest.mark.asyncio
async def test_ac_b3_parked_transport_exhausted_chain_no_announce() -> None:
    """AC-B3: PARKED_TRANSPORT + harvest not owed over exhausted chain → no announce."""
    ledger = CursorDispatchLedger.instance()
    _crash_chain(ledger, count=3)
    dispatch_id = "ac-b3-parked"
    _admit_conductor(ledger, _req(dispatch_id=dispatch_id))
    ledger.merge_record_json(
        dispatch_id=dispatch_id,
        patch={
            "closeout_stop_tokens": ["PARKED_TRANSPORT"],
            "closeout_harvest_owed": False,
            "hop_entry_gate": "G4",
        },
    )
    ledger.mark_terminal(dispatch_id=dispatch_id, terminal_status="completed")
    with (
        patch(
            "services.git_integration_worker.cursor_sdk_closeout.conductor_hop_park.default_park_poster",
        ) as park_poster,
        patch(
            "services.git_integration_worker.cursor_sdk_closeout.conductor_hop.post_conductor_hop_team_dispatch",
            AsyncMock(),
        ),
    ):
        await maybe_fire_conductor_hop_reactor(dispatch_id=dispatch_id)
    park_poster.assert_not_called()


@pytest.mark.asyncio
async def test_ac_b4_exhausted_chain_live_sibling_skipped() -> None:
    """AC-B4: exhausted crash chain + live sibling → live_sibling skip, no park."""
    ledger = CursorDispatchLedger.instance()
    last_crash = _crash_chain(ledger, count=3)
    dispatch_id = "ac-b4-current"
    _admit_conductor(ledger, _req(dispatch_id=dispatch_id))
    ledger.merge_record_json(
        dispatch_id=dispatch_id,
        patch={"closeout_stop_tokens": [], "hop_entry_gate": "G4"},
    )
    ledger.mark_terminal(dispatch_id=dispatch_id, terminal_status="failed")
    skipped_gates: list[str] = []
    with (
        patch(
            "services.git_integration_worker.cursor_sdk_closeout.conductor_hop.live_conductor_row_on_thread",
            return_value=True,
        ),
        patch(
            "services.git_integration_worker.cursor_sdk_closeout.conductor_hop_park.default_park_poster",
        ) as park_poster,
        patch(
            "services.git_integration_worker.cursor_sdk_closeout.conductor_hop.post_conductor_hop_team_dispatch",
            AsyncMock(),
        ),
        patch(
            "services.git_integration_worker.cursor_sdk_closeout.conductor_hop.emit_frontier_sdk_conductor_hop_skipped",
            side_effect=lambda **kw: skipped_gates.append(kw["gate"]),
        ),
    ):
        await maybe_fire_conductor_hop_reactor(dispatch_id=dispatch_id)
    park_poster.assert_not_called()
    assert "live_sibling" in skipped_gates
    assert last_crash  # chain fixture used


@pytest.mark.asyncio
async def test_ac_b5_evaluate_hop_budget_at_most_once_on_announce_path() -> None:
    """AC-B5: evaluate_hop_budget runs at most once when announcing park."""
    ledger = CursorDispatchLedger.instance()
    for idx in range(1, 4):
        req = _req(dispatch_id=f"ac-b5-{idx}")
        _admit_conductor(
            ledger,
            req,
            record_patch={"hop_entry_gate": "G4", "hop_witnessed_done": []},
        )
        ledger.mark_terminal(dispatch_id=f"ac-b5-{idx}", terminal_status="failed")
    dispatch_id = "ac-b5-3"
    with (
        patch(
            "services.git_integration_worker.cursor_sdk_closeout.conductor_hop_budget.load_hop_budget_config",
            return_value=HopBudgetConfig(
                crash_cap_per_row=3,
                no_progress_cap=2,
                mission_cap=24,
                crash_backoff_s=(30.0, 120.0, 300.0),
                reactor_grace_s=120.0,
            ),
        ),
        patch(
            "services.git_integration_worker.cursor_sdk_closeout.conductor_hop.evaluate_hop_budget",
            wraps=evaluate_hop_budget,
        ) as budget_eval,
        patch(
            "services.git_integration_worker.cursor_sdk_closeout.conductor_hop_park.default_park_poster",
        ),
        patch(
            "services.git_integration_worker.cursor_sdk_closeout.conductor_hop_park.page_hop_budget_parked",
            AsyncMock(return_value=True),
        ),
    ):
        await maybe_fire_conductor_hop_reactor(dispatch_id=dispatch_id)
    assert budget_eval.call_count <= 1


@pytest.mark.offline
def test_ac_p1_5_rematerialize_context_roundtrip() -> None:
    """AC-P1-5 — hop body A5 keys round-trip through RematerializeContext."""
    from implement_admission.conductor_materialize import (
        rematerialize_context_from_dispatch,
    )

    ledger = CursorDispatchLedger.instance()
    row = _terminal_row(ledger, closeout_tokens=["ROW_HOP"])
    ledger.merge_record_json(
        dispatch_id="pred-hop-1",
        patch={"summoning_thread_id": "10223"},
    )
    with ledger._connect() as conn:
        refreshed = conn.execute(
            "SELECT * FROM cursor_sdk_dispatches WHERE dispatch_id='pred-hop-1'"
        ).fetchone()
    row = {k: refreshed[k] for k in refreshed.keys()}
    body = build_hop_team_dispatch_body(row)
    assert body is not None
    ctx = rematerialize_context_from_dispatch(
        hop_seq=body.get("hop_seq"),
        hop_from=body.get("hop_from"),
        dispatch_thread_id=body.get("dispatch_thread_id"),
        generation_options=body.get("generation_options"),
    )
    assert ctx is not None
    assert ctx.hop_seq == body["hop_seq"]
    assert ctx.predecessor_dispatch_id == body["hop_from"]
    assert ctx.summoning_thread_id == "10223"
    assert ctx.summoning_thread_id_unresolved is False


@pytest.mark.offline
def test_ac_p1_6_hop_no_worker_thread_substitution() -> None:
    """AC-P1-6 — empty ledger summoning_thread_id must not become worker thread_id."""
    ledger = CursorDispatchLedger.instance()
    row = _terminal_row(ledger, closeout_tokens=["ROW_HOP"])
    body = build_hop_team_dispatch_body(row)
    assert body is not None
    assert "dispatch_thread_id" not in body
    assert body["generation_options"]["summoning_thread_id_unresolved"] is True


@pytest.mark.offline
def test_ac_p1_6_hop_carries_ledger_summoning_thread() -> None:
    """AC-P1-6 — predecessor summoning_thread_id flows to hop generation_options."""
    ledger = CursorDispatchLedger.instance()
    row = _terminal_row(ledger, closeout_tokens=["ROW_HOP"])
    ledger.merge_record_json(
        dispatch_id="pred-hop-1",
        patch={"summoning_thread_id": "10223"},
    )
    with ledger._connect() as conn:
        refreshed = conn.execute(
            "SELECT * FROM cursor_sdk_dispatches WHERE dispatch_id='pred-hop-1'"
        ).fetchone()
    row = {k: refreshed[k] for k in refreshed.keys()}
    body = build_hop_team_dispatch_body(row)
    assert body is not None
    assert body["dispatch_thread_id"] == "10223"
    assert body["generation_options"]["summoning_thread_id"] == "10223"
    assert "summoning_thread_id_unresolved" not in body["generation_options"]


def test_utc_closeout_instant_returns_non_empty_iso_timestamp() -> None:
    instant = _utc_closeout_instant()
    assert instant
    assert "T" in instant
