"""CSE seating hook — hop occupy path + AC4 path-scoped regression."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from hop_handoff import StandingHandoffFreshness, build_continuity_handoff_body

from services.git_integration_worker.cursor_dispatch_ledger import (
    CURSOR_SDK_DISPATCH_LEDGER_ENV,
    CursorDispatchLedger,
)

pytestmark = pytest.mark.offline


@pytest.fixture()
def ledger(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> CursorDispatchLedger:
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv(
        CURSOR_SDK_DISPATCH_LEDGER_ENV, str(tmp_path / "cursor-sdk-dispatch.db")
    )
    CursorDispatchLedger._instance = None
    return CursorDispatchLedger.instance()


_OCCUPY_URL = "https://claude.ai/cowork/cse_occupyhop1"
_PREDECESSOR_URL = "https://claude.ai/cowork/cse_predecessor1"
_LANE = "99001"


def _hop_body(*, occupy: str | None = _OCCUPY_URL, superseded: str = "reg-old") -> str:
    return build_continuity_handoff_body(
        thread_id=_LANE,
        trigger="test-hop",
        source="agent-bus-hop-verb",
        handoff=StandingHandoffFreshness(
            status="current",
            uri=f"cortex://notes/system/threads/{_LANE}-standing-handoff.md",
            mtime_epoch=1.0,
            age_s=1.0,
        ),
        occupy_target=occupy,
        superseded_registration_id=superseded,
        successor_birth_id="c" * 32,
    )




def test_handoff_emits_occupy_target_not_you_are() -> None:
    body = _hop_body()
    lines = body.splitlines()
    assert any(line.startswith("occupy_target:") for line in lines)
    assert not any(line.startswith("you_are:") for line in lines)


















_LANE_T = "12286"
_OCCUPY_REG = "c97c246d71884a03a0ed50cb83471e4b"
_SUCCESSOR_7_URL = "https://claude.ai/cowork/cse_successor7"
_SUCCESSOR_8_URL = "https://claude.ai/cowork/cse_successor8"


def _census_row(registration_id: str) -> dict[str, str]:
    return {
        "registration_id": registration_id,
        "parent_thread": _LANE_T,
        "purpose": "operator-proxy",
        "seat_state": "active",
        "stream_state": "running",
        "execution_id": f"exec-{registration_id}",
        "source": "cse-session-registry",
    }






def test_wire_id_outside_census_refused_and_empty_census_flags_mismatch() -> None:
    """AC3: non-empty miss refuses; empty census admits with census_mismatch."""
    from claude_bundles.request_admission_identity import gate_request_admission

    occupied = {
        "rows": [],
        "seated_rows": [_census_row("successor-8")],
    }
    with patch(
        "claude_bundles.hop_seat_cutover.load_watches",
        return_value={_LANE_T: {"thread_id": _LANE_T}},
    ):
        refused = gate_request_admission(
            thread_id=_LANE_T,
            caller_registration_id=_OCCUPY_REG,
            active_work_snap=occupied,
        )
    assert refused is not None
    assert refused["code"] == "seat.wire_id_not_in_census"
    assert refused["data"]["reason"] == "wire_id_not_in_census"

    audit: dict[str, object] = {}
    with patch(
        "claude_bundles.hop_seat_cutover.load_watches",
        return_value={_LANE_T: {"thread_id": _LANE_T}},
    ):
        admitted = gate_request_admission(
            thread_id=_LANE_T,
            caller_registration_id="attended-wire",
            active_work_snap={"rows": [], "seated_rows": []},
            audit=audit,
        )
    assert admitted is None
    assert audit["census_mismatch"] is True




def _isolate_registry(monkeypatch: pytest.MonkeyPatch, root: Path) -> None:
    """Point the CDP registry store at *root* so a seating test cannot touch live."""
    import claude_bundles.cdp_registry_store as store

    root.mkdir(parents=True, exist_ok=True)
    regs = root / "registrations"
    regs.mkdir()
    monkeypatch.setattr(store, "REGISTRY_DIR", root)
    monkeypatch.setattr(store, "REGISTRY_LOG", root / "registry.jsonl")
    monkeypatch.setattr(store, "ACTIVE_JSON", root / "active.json")
    monkeypatch.setattr(store, "PORTS_LOCK", root / "ports.lock")
    monkeypatch.setattr(store, "REGISTRATIONS_DIR", regs)
    monkeypatch.setenv("CDP_REGISTRY_SEAT_AUTHORITY", "1")






def test_watch_retired_ids_leave_successor_as_sole_census_match() -> None:
    from claude_bundles.request_admission_identity import (
        resolve_request_admission_identity,
    )

    snap = {
        "rows": [],
        "seated_rows": [_census_row("successor-7"), _census_row("successor-8")],
    }
    with (
        patch(
            "claude_bundles.hop_seat_cutover.load_watches",
            return_value={
                _LANE_T: {
                    "thread_id": _LANE_T,
                    "retired_registration_ids": ["successor-7"],
                }
            },
        ),
        patch(
            "claude_bundles.request_admission_identity._resolve_origin_cse_registration",
            return_value=None,
        ),
    ):
        identity = resolve_request_admission_identity(
            thread_id=_LANE_T,
            caller_registration_id=None,
            active_work_snap=snap,
        )
    assert identity.census_n == 1
    assert identity.match_registration_ids == ("successor-8",)
    assert identity.registration_id == "successor-8"




def test_resolve_hop_successor_registration_id_prefers_op_row() -> None:
    from claude_bundles.request_admission_census import (
        resolve_hop_successor_registration_id,
    )

    exec_id = "140c033e-6e8f-4072-8ef7-ba1c06ad3d3f"
    mint = "ec59f47871884a03a0ed50cb83471e4b"
    snap = {
        "rows": [
            {
                "registration_id": mint,
                "parent_thread": _LANE_T,
                "purpose": "operator-proxy",
                "stream_state": "running",
                "execution_id": exec_id,
            }
        ],
        "seated_rows": [],
    }
    assert (
        resolve_hop_successor_registration_id(
            snap, parent_thread=_LANE_T, execution_id=exec_id
        )
        == mint
    )










_PRED = "662daf5d-c192-46fa-bec3-066aa4284f1a"
_SUCC = "8dcc0993-8464-46a8-b31c-2e95c8f3c13a"
_CHAT_DISPATCH = "a2e67ddc-5d11-4c71-aa4a-cc0350150114"
_STAMP_LANE = "12286"


class _TerminateCapture:
    def __init__(self) -> None:
        self.posts: list[dict[str, object]] = []

    def post(
        self, path: str, json: dict[str, object] | None = None, headers: object = None
    ) -> object:
        del headers
        self.posts.append({"path": path, "json": json or {}})

        class _Resp:
            status_code = 200
            text = ""

        return _Resp()

    def __enter__(self) -> _TerminateCapture:
        return self

    def __exit__(self, *args: object) -> bool:
        return False


def _install_terminate_capture(monkeypatch: pytest.MonkeyPatch) -> _TerminateCapture:
    client = _TerminateCapture()
    monkeypatch.setattr(
        "transport_utils.make_sync_client",
        lambda *_args, **_kwargs: client,
    )
    return client






def _link_terminal(execution_id: str) -> str | None:
    import os
    import sqlite3

    conn = sqlite3.connect(os.environ["AGENT_BUS_DB_PATH"])
    try:
        row = conn.execute(
            "SELECT terminal_status FROM thread_dispatch_links WHERE execution_id=?",
            (execution_id,),
        ).fetchone()
    finally:
        conn.close()
    assert row is not None
    return row[0]












