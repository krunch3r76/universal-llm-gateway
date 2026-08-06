"""R-admit consult-seat hosting — dual-wire under window_kind=consult."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from scripts.model_manager.ui.controller.charter_runner.checkpoint_schema import (
    parse_checkpoint,
)
from scripts.model_manager.ui.controller.charter_runner.admission import (
    evaluate_root,
)
from scripts.model_manager.ui.controller.charter_runner.executor_defaults import (
    consult_handoff_body,
    consult_host_generate_body,
    r_admit_consult_generate_body,
)
from scripts.model_manager.charter_control.r_verdict_gate import (
    consult_provenance_from_r_admit,
)

_FIXTURE_J = """\
# CHECKPOINT — CONSULT_PENDING

## Steps
1. [x] Q complete
2. [ ] Implement after consult

## In-flight / WIP
none

## Next pickup
1. CONSULT_PENDING — judgment gap on G4 implement bind · consult_role: judgment_gap

## Frictions
_None this window._

## Sidecars
_None this window._

Scoreboard: cortex://notes/system/threads/5555-charter-scoreboard.md

— RESUME (any seat, no command): load agent-bus-discipline body → do not read linearly.
"""

_FIXTURE_R = """\
# CHECKPOINT — CONSULT_PENDING (G3 R-admit)

## Steps
1. [x] G2 dense spec
2. [ ] G4 implement after R

## In-flight / WIP
none

## Next pickup
1. CONSULT_PENDING — G3 R-admit · consult_role: r_admit
   prompt_uri: cortex://notes/system/specs/foo-dense.md

## Frictions
_None this window._

## Sidecars
_None this window._

Scoreboard: cortex://notes/system/threads/5609-charter-scoreboard.md

— RESUME (any seat, no command): load agent-bus-discipline body → do not read linearly.
"""

_FIXTURE_R_OF2 = """\
# CHECKPOINT — CONSULT_PENDING mid-poll

## Steps
1. [~] G3 — R-admit poll

## In-flight / WIP
none

## Next pickup
1. CONSULT_PENDING — G3 resume R-admit poll (execution_id=abc123def) · consult_role: r_admit

## Frictions
_None this window._

## Sidecars
_None this window._

Scoreboard: cortex://notes/system/threads/5609-charter-scoreboard.md

— RESUME (any seat, no command): load agent-bus-discipline body → do not read linearly.
"""


def _turn(n: int, subject: str, body: str) -> dict[str, Any]:
    return {"turn_number": n, "subject": subject, "body": body}


@pytest.mark.offline
def test_parse_consult_role_explicit() -> None:
    j = parse_checkpoint(_FIXTURE_J)
    assert j.consult_pending is True
    assert j.consult_role == "judgment_gap"

    r = parse_checkpoint(_FIXTURE_R)
    assert r.consult_pending is True
    assert r.consult_role == "r_admit"


@pytest.mark.offline
def test_parse_consult_pending_state_section_without_next_pickup_prefix() -> None:
    """5975 turn-26 shape: CONSULT_PENDING in ## State, Next-pickup names G3 only."""
    body = """\
TYPE: CHECKPOINT

## State
**WIP:** none.

CONSULT_PENDING
consult_role: r_admit

## Next pickup
1. G3 — R-admit · consult_role: r_admit · todo:cursor-auto-in-seat-nested-terminal · executor_lane: judgment

Scoreboard: cortex://notes/system/threads/5975-charter-scoreboard.md
"""
    parsed = parse_checkpoint(body)
    assert parsed.consult_pending is True
    assert parsed.consult_role == "r_admit"


@pytest.mark.offline
def test_parse_consult_pending_cleared_in_state_is_inactive() -> None:
    """5975 turn-31 shape: ``CONSULT_PENDING cleared`` is not an active stop class."""
    body = """\
TYPE: CHECKPOINT

## State
CONSULT_PENDING cleared.

## Consult provenance
- consult_thread: agent-bus:5975#turn-30
- verdict: ADMIT_WITH_AMENDMENTS

## Next pickup
1. G4 — Stage-B implement · todo:cursor-auto-in-seat-nested-terminal · executor_lane: implement

## WIP / In-flight
none
"""
    parsed = parse_checkpoint(body)
    assert parsed.consult_pending is False
    assert parsed.executor_lane == "implement"


@pytest.mark.offline
def test_parse_consult_role_defaults_without_explicit_marker() -> None:
    body = _FIXTURE_J.replace(" · consult_role: judgment_gap", "")
    parsed = parse_checkpoint(body)
    assert parsed.consult_role == "judgment_gap"

    r_body = _FIXTURE_R.replace(" · consult_role: r_admit", "")
    r_parsed = parse_checkpoint(r_body)
    assert r_parsed.consult_role == "judgment_gap"


@pytest.mark.offline
def test_bidirectional_misclassification_guard() -> None:
    """Explicit role wins over sniff markers in the other direction."""
    j_with_r_word = _FIXTURE_J.replace(
        "consult_role: judgment_gap",
        "consult_role: judgment_gap · R-admit wording only",
    )
    assert parse_checkpoint(j_with_r_word).consult_role == "judgment_gap"

    r_with_j_word = _FIXTURE_R.replace(
        "consult_role: r_admit",
        "consult_role: r_admit · not a judgment gap",
    )
    assert parse_checkpoint(r_with_j_word).consult_role == "r_admit"


@pytest.mark.offline
def test_judgment_and_r_admit_packets_both_host_cdp() -> None:
    from scripts.model_manager.ui.controller.charter_runner.window_exec import (
        materialize_consult_packet,
    )

    j_packet = materialize_consult_packet(
        "5555", parse_checkpoint(_FIXTURE_J), window_index=2
    )
    r_packet = materialize_consult_packet(
        "5609", parse_checkpoint(_FIXTURE_R), window_index=3
    )
    # Both roles: cursor-sdk host fires cdp/opus-5 (auto-wake; a:26476).
    for packet in (j_packet, r_packet):
        assert "cdp/opus-5" in packet
        assert "team_dispatch" in packet
        assert "project_ask" in packet  # escape
        assert "poll_hint" in packet or "from=web-anthropic" in packet
        primary_idx = packet.find("cdp/opus-5")
        escape_idx = packet.lower().find("escape")
        submit_idx = packet.find("project_ask(op=submit")
        assert primary_idx >= 0
        assert escape_idx > primary_idx
        assert submit_idx < 0 or submit_idx > escape_idx
    assert "consultant_family=anthropic" in r_packet
    assert "consult_provenance_from_r_admit" in r_packet
    assert "judgment_gap" in j_packet
    assert "IF6" in j_packet


@pytest.mark.offline
def test_autonomous_g3_no_holder_project_ask() -> None:
    from scripts.model_manager.ui.controller.charter_runner.window_exec import (
        materialize_autonomous_packet,
    )
    from scripts.model_manager.ui.controller.tests.test_charter_runner import (
        _CHECKPOINT_BODY,
    )

    packet = materialize_autonomous_packet(
        "5609", parse_checkpoint(_CHECKPOINT_BODY), window_index=1
    )
    assert "consult_role: r_admit" in packet
    assert (
        "self-fire" in packet
        or "does NOT fire cdp/" in packet
        or "does NOT use project_ask at G3" in packet
    )
    assert "project_ask(op=submit" not in packet
    assert "cdp/opus-5" in packet
    assert "IF6" in packet


@pytest.mark.offline
def test_dual_wire_admission_bodies_unified_host() -> None:
    """judgment_gap and r_admit share the CDP-host generate wire (a:26476).

    Attended handoff body remains available but is not the autonomous tick path.
    """
    common = dict(
        root_id="5609",
        window_index=2,
        packet_path="tmp/charter-runner/5609-w2.md",
        subject="test",
        caller_agent="charter-runner",
    )
    host = consult_host_generate_body(**common)
    r_body = r_admit_consult_generate_body(**common)
    legacy = consult_handoff_body(**common)
    assert host == r_body
    assert host["op"] == "generate"
    assert host["seat"] == "cursor-sdk"
    assert host["read_only"] is True
    assert legacy["op"] == "handoff"
    assert legacy["role"] == "web-consult"


@pytest.mark.offline
def test_of2_resume_eligible_consult_r_wire() -> None:
    parsed = parse_checkpoint(_FIXTURE_R_OF2)
    assert parsed.consult_pending is True
    assert parsed.consult_role == "r_admit"
    decision = evaluate_root(
        "5609",
        [_turn(5, "CHECKPOINT", _FIXTURE_R_OF2)],
        __import__(
            "scripts.model_manager.ui.controller.charter_runner.admission",
            fromlist=["CapStore"],
        ).CapStore(),
    )
    assert decision.eligible is True
    assert decision.window_kind == "consult"
    assert decision.parsed is not None
    assert decision.parsed.consult_role == "r_admit"


@pytest.mark.offline
def test_select_packet_branches_on_consult_role() -> None:
    from scripts.model_manager.ui.controller.charter_runner.window_exec import (
        select_packet,
    )

    j_parsed = parse_checkpoint(_FIXTURE_J)
    r_parsed = parse_checkpoint(_FIXTURE_R)
    j_packet, j_subj = select_packet(
        "5555", j_parsed, scoreboard_uri=None, window_index=1, admission_mode="consult"
    )
    r_packet, r_subj = select_packet(
        "5609", r_parsed, scoreboard_uri=None, window_index=2, admission_mode="consult"
    )
    assert "judgment_gap" in j_subj or "cdp/opus-5" in j_subj
    assert "cdp/opus-5" in j_packet
    assert "project_ask" in j_packet
    assert "r_admit" in r_subj
    assert "cdp/opus-5" in r_packet
    assert "project_ask" in r_packet


@pytest.mark.offline
def test_fire_window_consult_dual_wire(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from scripts.model_manager.ui.controller.charter_runner import (
        dispatch_client as dc,
    )

    posted: list[tuple[str, dict[str, Any]]] = []

    class _FakeResp:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, Any]:
            return {"thread_id": "9005", "execution_id": "exec-9005"}

    class _FakeClient:
        async def post(self, path: str, *, json: dict[str, Any]) -> _FakeResp:
            posted.append((path, json))
            return _FakeResp()

        async def __aenter__(self) -> _FakeClient:
            return self

        async def __aexit__(self, *_a: object) -> None:
            return None

    monkeypatch.setattr(dc, "make_async_client", lambda *_a, **_k: _FakeClient())

    async def _run(consult_role: str | None) -> dict[str, Any]:
        return await dc.fire_window(
            "5609",
            "<scope>x</scope>",
            workspace_root=tmp_path,
            window_index=1,
            admission_mode="consult",
            consult_role=consult_role,
        )

    j_result = asyncio.run(_run("judgment_gap"))
    assert posted[-1][0] == "/api/v1/team/dispatch"
    assert posted[-1][1]["op"] == "generate"
    assert posted[-1][1]["read_only"] is True
    assert j_result["executor"]["consult_role"] == "judgment_gap"
    assert j_result["executor"]["reviewer_model"] == "cdp/opus-5"

    r_result = asyncio.run(_run("r_admit"))
    assert posted[-1][0] == "/api/v1/team/dispatch"
    assert posted[-1][1]["op"] == "generate"
    assert r_result["executor"]["consult_role"] == "r_admit"


@pytest.mark.offline
def test_consult_provenance_reviewer_family() -> None:
    prov = consult_provenance_from_r_admit(
        consult_thread="agent-bus:5610",
        harvest_text="Merits verdict: ADMIT",
    )
    assert prov is not None
    assert prov.consultant_family == "anthropic"
    assert prov.consultant_substrate == "web-anthropic"
    assert prov.verdict == "ADMIT"


@pytest.mark.offline
@pytest.mark.skip(reason="Phase 3: old evaluate_root→admit_window path retired; needs ledger-seeded kernel port")
def test_tick_admits_r_admit_consult_generate_wire(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from scripts.model_manager.ui.controller.charter_runner import (
        dispatch_client as dc,
    )
    from scripts.model_manager.ui.controller.charter_runner import r_corpus_sha as rcs
    from scripts.model_manager.ui.controller.charter_runner import kernel as tl
    from scripts.model_manager.ui.controller.charter_runner import window_log as wl
    from scripts.model_manager.ui.controller.charter_runner.admission import CapStore
    from scripts.model_manager.ui.controller.shutdown_gate import ManageShutdownGate
    from scripts.model_manager.ui.model.service_state import ServiceInfo, ServiceStatus

    monkeypatch.setattr(wl, "LOG_DIR", tmp_path / "cr")
    monkeypatch.setattr(wl, "_HARVESTED_DIR", tmp_path / "cr" / "harvested")

    # R-admit admission is gated on a Sidecars spec_sha256 pin matching live
    # dense-spec bytes, so the fixture must carry a pin over a real file.
    cortex_root = tmp_path / "cortex"
    spec_path = cortex_root / "notes" / "system" / "specs" / "foo-dense.md"
    spec_path.parent.mkdir(parents=True)
    spec_path.write_text("# foo dense spec\n", encoding="utf-8")
    monkeypatch.setattr(rcs, "cortex_files_root", lambda: cortex_root)
    pin_row = (
        f"- cortex://notes/system/specs/foo-dense.md — dense spec · "
        f"spec_sha256:{rcs.file_sha256_hex(spec_path)}"
    )
    checkpoint_r = _FIXTURE_R.replace(
        "## Sidecars\n_None this window._",
        f"## Sidecars\n{pin_row}",
    )

    fired: list[tuple[str, str | None]] = []
    turns_state = [_turn(2, "CHECKPOINT", checkpoint_r)]

    async def fake_roots() -> list[dict[str, Any]]:
        return [{"id": "5609"}]

    async def fake_turns(root_id: str) -> list[dict[str, Any]]:
        return list(turns_state)

    async def fake_thread(_thread_id: str) -> dict[str, Any]:
        return {"tags": []}

    async def fake_pointer(
        root_id: str,
        *,
        window_index: int,
        posted_at_iso: str,
        worker_thread: str = "",
        packet_path: str = "",
    ) -> dict:
        turns_state.append(_turn(3, f"WIP charter-runner window {window_index}", "{}"))
        return {}

    async def fake_fire(
        root_id: str,
        packet_text: str,
        *,
        workspace_root: Path,
        window_index: int = 1,
        subject: str | None = None,
        admission_mode: str = "generate",
        consult_role: str | None = None,
        implement_source_ref: str | None = None,
    ) -> dict:
        fired.append((admission_mode, consult_role))
        assert admission_mode == "consult"
        assert consult_role == "r_admit"
        # Consult windows are never routed to the implement bind (review §5).
        assert implement_source_ref is None
        assert "project_ask" in packet_text
        assert "cdp/opus-5" in packet_text
        return {"dispatch_id": "w-r", "thread_id": "w-r", "push_reminder": ""}

    state = MagicMock()
    state.check_cortex_api.return_value = ServiceInfo(
        name="cortex", status=ServiceStatus.RUNNING
    )
    state.check_agent_bus.return_value = ServiceInfo(
        name="agent_bus", status=ServiceStatus.RUNNING
    )

    monkeypatch.setattr(tl.bus_client, "list_enrolled_roots", fake_roots)
    monkeypatch.setattr(tl.bus_client, "fetch_turns", fake_turns)
    monkeypatch.setattr(tl.bus_client, "fetch_thread", fake_thread)
    monkeypatch.setattr(tl.bus_client, "post_admission_pointer", fake_pointer)
    monkeypatch.setattr(dc, "fire_window", fake_fire)

    async def _giw_not_busy() -> bool:
        return False

    monkeypatch.setattr(tl, "probe_giw_live_hold", _giw_not_busy)

    loop = tl.CharterRunnerTickLoop(
        service_state=state,
        shutdown_gate=ManageShutdownGate(),
        workspace_root=Path("/tmp/workspace"),
        tick_interval_s=0.01,
        caps=CapStore(intent_dir=tmp_path / "intent"),
    )

    async def _exercise() -> None:
        await loop.start()
        await asyncio.sleep(0.05)
        await loop.stop()

    asyncio.run(_exercise())
    assert fired == [("consult", "r_admit")]
