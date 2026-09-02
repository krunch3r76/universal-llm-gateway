"""Regression: window_terminal_contract tip classification + arc derivation."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest

from scripts.model_manager.ui.controller.charter_runner.admission import CapStore
from scripts.model_manager.ui.controller.charter_runner.checkpoint_admit_gate import (
    validate_arc_for_admit,
)
from scripts.model_manager.ui.controller.charter_runner.checkpoint_schema import (
    parse_checkpoint,
)
from scripts.model_manager.ui.controller.charter_runner.admission import (
    evaluate_root,
)
from scripts.model_manager.ui.controller.charter_runner.harvest import (
    completed_windows,
)
from scripts.model_manager.ui.controller.charter_runner.window_terminal_contract import (
    WINDOW_TERMINALS,
    effective_required_arc,
    is_tip_class,
    parse_stop_vocabulary_window_terminals,
    required_arc,
    terminal_verb,
)
from scripts.model_manager.ui.controller.charter_runner.checkpoint_schema import (
    append_footer_to_packet,
    footer_kwargs_for_window,
)

_CONSULT_SUBJECT = (
    "CONSULT_PENDING wave 2 — G3 r_admit · git-worker-restart anti-pattern"
)
_CONSULT_BODY = """\
TYPE: CHECKPOINT

## Profile
`tick_charter` (charter-runner enrolled)

## Anchor
- Thread: agent-bus:5975
- Scoreboard: cortex://notes/system/threads/5975-charter-scoreboard.md
- Todo: todo:g3-r-admit · workflow_state=implement_ready
- Window: 2 · worker thread 5977

## State
CONSULT_PENDING
consult_role: r_admit
primary_transport: team_dispatch(model=cdp/opus-5)

## Steps
1. [x] G2 — dense spec
2. [ ] G3 — R-admit (consult seat)

## WIP / In-flight
_None this window._

## Next pickup
1. CONSULT_PENDING — G3 R-admit · consult_role: r_admit · executor_lane: judgment

## Frictions
_None this window._

## Sidecars
- Dense spec: cortex://notes/system/specs/foo.md · spec_sha256:2429f124f48ed3a3bb0d50b0a32f11e78b354952bf5c29243e68043380c08182
- R prompt: cortex://notes/system/threads/foo-r-prompt.md

## What happened (plain)
Densified the spec; consult seat owns R-admit next.

Scoreboard: cortex://notes/system/threads/5975-charter-scoreboard.md

— RESUME (any seat, no command): load agent-bus-discipline body → do not read linearly.
"""

_SPEC_STOP_VOCAB_EXCERPT = """\
### Stop vocabulary (bound)

| Verb / class | Meaning | Recovery |
|---|---|---|
| `CHECKPOINT` (clean) | Window success-shaped boundary | next tick admits pickup |
| `CONSULT_PENDING` | External consult required; corpus pinned; holder stopped | tick admits consult seat (depth-1) |
| `BLOCKED` | Human needed | operator |
| `PACKAGING_DEFICIT` | Receiver bounced corpus | repackage budget (Phase C) |
| `verifier_reject` | R RETURN / SCOPE-DRIFT / independence fail | revise-within-counter or BLOCKED |
"""


def _turn(n: int, subject: str, body: str = "") -> dict[str, Any]:
    return {"turn_number": n, "subject": subject, "body": body}


def _w2_turns() -> list[dict[str, Any]]:
    consult_body = append_footer_to_packet(
        _CONSULT_BODY, **footer_kwargs_for_window("5975", 2, status="CONSULT_PENDING")
    )
    return [
        _turn(4, "CHECKPOINT wave 1 — G1 investigate done · G2 next", _CONSULT_BODY),
        _turn(
            6,
            "WIP charter-runner window 2",
            '{"charter_runner":true,"window":2,"worker_thread":"5977"}',
        ),
        _turn(7, _CONSULT_SUBJECT, consult_body),
    ]


@pytest.mark.offline
def test_window_terminals_spec_parity() -> None:
    # Scoping: first four subject-prefix terminal rows only — not reason codes
    # (verifier_reject, transient_infra, …) that share §Stop vocabulary table.
    from_spec = parse_stop_vocabulary_window_terminals(_SPEC_STOP_VOCAB_EXCERPT)
    assert from_spec == WINDOW_TERMINALS


@pytest.mark.offline
def test_is_tip_class_accepts_consult_pending_subject() -> None:
    assert is_tip_class(_CONSULT_SUBJECT) is True
    assert terminal_verb(_CONSULT_SUBJECT) == "CONSULT_PENDING"
    assert is_tip_class("CHECKPOINT wave 1") is True
    assert is_tip_class("BLOCKED — revise cap") is True
    assert is_tip_class("WIP charter-runner window 2") is False


@pytest.mark.offline
def test_pickup_append_is_tip_class_but_not_window_terminal() -> None:
    """Conveyor pickup appends must not close windows (6110 w31–33 storm)."""
    from scripts.model_manager.ui.controller.charter_runner.window_terminal_contract import (
        is_pickup_append,
        is_window_terminal,
    )

    subject = "CHECKPOINT — charter-friction-conveyor pickup append"
    assert is_pickup_append(subject) is True
    assert is_tip_class(subject) is True
    assert is_window_terminal(subject) is False
    assert terminal_verb(subject) is None


@pytest.mark.offline
def test_completed_windows_skips_pickup_append_as_closeout() -> None:
    """Admission + pickup append must not pair as a harvested window."""
    turns = [
        _turn(10, "CHECKPOINT wave 0 — seed", "seed tip"),
        _turn(
            11,
            "WIP charter-runner window 31",
            '{"charter_runner":true,"window":31,"worker_thread":"x"}',
        ),
        _turn(12, "CHECKPOINT — charter-friction-conveyor pickup append", "append"),
        _turn(13, "CHECKPOINT — charter-friction-conveyor pickup append", "append2"),
        _turn(20, "CHECKPOINT wave 28 — G9 w31 done", "worker closeout"),
    ]
    pairs = completed_windows(turns)
    assert len(pairs) == 1
    admission, terminal = pairs[0]
    assert admission["turn_number"] == 11
    assert terminal["turn_number"] == 20
    assert "pickup append" not in str(terminal["subject"]).lower()


@pytest.mark.offline
def test_completed_windows_pairs_latest_terminal_after_admit_not_first() -> None:
    """Post-admit birth CHECKPOINT thrash must not poison harvest pairing (6237).

    First-following pairing glued w1 to a fence-less birth tip forever; reject
    markers then skipped every tick while a later footer-valid closeout sat
    unread. Pair with the latest terminal inside the admission window instead.
    """
    turns = [
        _turn(1, "CHECKPOINT — charter birth (wave 0)", "birth pre-admit"),
        _turn(
            12,
            "WIP charter-runner window 1",
            '{"charter_runner":true,"window":1,"worker_thread":"6238"}',
        ),
        _turn(13, "CHECKPOINT — charter birth (wave 0)", "birth thrash post-admit"),
        _turn(14, "CHECKPOINT — charter birth (wave 0)", "birth thrash again"),
        _turn(16, "CHECKPOINT wave 1 — G1 Q done; next G2 A+Gate-2", "real closeout"),
        _turn(26, "CHECKPOINT wave 1 — G1 Q done; next G2 A+Gate-2", "closeout tip"),
        _turn(27, "DIRECTIVE — G5 executor-primaries", "operator note, not terminal"),
    ]
    pairs = completed_windows(turns)
    assert len(pairs) == 1
    admission, terminal = pairs[0]
    assert admission["turn_number"] == 12
    assert terminal["turn_number"] == 26
    assert "wave 1" in str(terminal["subject"])


@pytest.mark.offline
def test_completed_windows_latest_stays_inside_admission_window() -> None:
    """Latest terminal must not cross into a later admission's span."""
    turns = [
        _turn(
            10,
            "WIP charter-runner window 1",
            '{"charter_runner":true,"window":1,"worker_thread":"a"}',
        ),
        _turn(11, "CHECKPOINT — birth thrash", "poison"),
        _turn(12, "CHECKPOINT wave 1 — w1 closeout", "w1 tip"),
        _turn(
            20,
            "WIP charter-runner window 2",
            '{"charter_runner":true,"window":2,"worker_thread":"b"}',
        ),
        _turn(21, "CHECKPOINT wave 2 — w2 closeout", "w2 tip"),
    ]
    pairs = completed_windows(turns)
    assert len(pairs) == 2
    assert pairs[0][0]["turn_number"] == 10
    assert pairs[0][1]["turn_number"] == 12
    assert pairs[1][0]["turn_number"] == 20
    assert pairs[1][1]["turn_number"] == 21


@pytest.mark.offline
def test_evaluate_root_window_in_flight_despite_pickup_append() -> None:
    """Pickup append after WIP must not clear window_in_flight (storm root)."""
    turns = [
        _turn(10, "CHECKPOINT wave 0 — seed tip advance", _CONSULT_BODY),
        _turn(
            11,
            "WIP charter-runner window 31",
            '{"charter_runner":true,"window":31,"worker_thread":"x"}',
        ),
        _turn(12, "CHECKPOINT — charter-friction-conveyor pickup append", "append"),
        _turn(13, "CHECKPOINT — charter-friction-conveyor pickup append", "append2"),
    ]
    decision = evaluate_root("6110", turns, CapStore())
    assert decision.eligible is False
    assert decision.reason == "window_in_flight"


@pytest.mark.offline
def test_is_tip_class_accepts_checkpoint_subject_with_consult_in_body() -> None:
    body = _CONSULT_BODY
    subject = "CHECKPOINT wave 2 — G2 densify done · consult next"
    assert is_tip_class(subject, body=body) is True
    assert terminal_verb(subject, body=body) == "CONSULT_PENDING"


@pytest.mark.offline
def test_completed_windows_pairs_admission_with_consult_pending_subject() -> None:
    pairs = completed_windows(_w2_turns())
    assert len(pairs) == 1
    admission, terminal = pairs[0]
    assert admission["turn_number"] == 6
    assert terminal["turn_number"] == 7
    assert str(terminal["subject"]).startswith("CONSULT_PENDING")


@pytest.mark.offline
def test_evaluate_root_consult_pending_subject_not_window_in_flight() -> None:
    decision = evaluate_root("5975", _w2_turns(), CapStore())
    assert decision.eligible is True
    assert decision.reason == "eligible_consult"
    assert decision.window_kind == "consult"
    assert decision.parsed is not None
    assert decision.parsed.consult_role == "r_admit"


@pytest.mark.offline
def test_required_arc_derivation_fail_closed() -> None:
    assert required_arc("mechanical") == "mechanical"
    assert required_arc("recon_pending") == "investigate"
    assert required_arc("judgment_required") == "r_admit_required"
    assert required_arc(None) == "r_admit_required"
    assert required_arc("unknown") == "r_admit_required"


_G3_MECHANICAL_BODY = """\
TYPE: CHECKPOINT

## Anchor
- Todo: todo:g3-window-terminal · workflow_state=implement_ready

## In-flight / WIP
none

## Next pickup
1. G3 — land the bind · executor_lane: implement

## Steps
1. [ ] G3 — implement

## Frictions
_None this window._

## Sidecars
_None this window._

— RESUME (any seat, no command): load agent-bus-discipline → scoreboard → CHECKPOINT.
"""

_CONSULT_PROVENANCE_BLOCK = """\
## Consult provenance
- consult_thread: agent-bus:5990
- verdict: ADMIT_WITH_AMENDMENTS
- consultant_model: claude-opus-5
- consultant_effort: high
- consultant_substrate: cdp/opus-5
"""

_G4_POST_R_ADMIT_BODY = _G3_MECHANICAL_BODY.replace(
    "## Frictions",
    f"{_CONSULT_PROVENANCE_BLOCK}\n## Frictions",
)


@pytest.mark.offline
def test_effective_required_arc_drops_to_mechanical_after_r_admit() -> None:
    assert (
        effective_required_arc(
            triage="judgment_required",
            executor_lane="implement",
            consult_pending=False,
            checkpoint_body=_G4_POST_R_ADMIT_BODY,
        )
        == "mechanical"
    )
    assert (
        effective_required_arc(
            triage="judgment_required",
            executor_lane="implement",
            consult_pending=False,
            checkpoint_body=_G3_MECHANICAL_BODY,
        )
        == "r_admit_required"
    )


@pytest.mark.offline
def test_validate_arc_allows_g4_implement_after_r_admit() -> None:
    parsed = parse_checkpoint(_G4_POST_R_ADMIT_BODY)
    verdict = validate_arc_for_admit(
        parsed,
        window_kind="worker",
        admission_mode="autonomous",
        consult_role=None,
        executor_lane="implement",
        checkpoint_body=_G4_POST_R_ADMIT_BODY,
        density_triage_lookup=lambda _ref: "judgment_required",
    )
    assert verdict is None


@pytest.mark.offline
def test_evaluate_root_allows_g4_implement_after_r_admit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    turns = [
        _turn(1, "CHECKPOINT wave 10 — G4 Stage-B implement", _G4_POST_R_ADMIT_BODY),
    ]
    monkeypatch.setenv("CHARTER_ADMISSION_MODE", "autonomous")
    monkeypatch.setattr(
        "scripts.model_manager.ui.controller.charter_runner.window_terminal_contract.default_density_triage_lookup",
        lambda _ref: "judgment_required",
    )
    decision = evaluate_root("5975", turns, CapStore())
    assert decision.reason != "arc_lane_too_weak"
    assert decision.eligible is True


_G2_JUDGMENT_BODY = """\
TYPE: CHECKPOINT

## Anchor
- Todo: todo:cursor-auto-in-seat-nested-terminal · workflow_state=implement_ready

## In-flight / WIP
none

## Next pickup
1. G2 — A + Gate-2 dense spec · executor_lane: judgment · todo:cursor-auto-in-seat-nested-terminal

## Steps
1. [ ] G2 — dense spec

## Frictions
_None this window._

## Sidecars
_None this window._

— RESUME (any seat, no command): load agent-bus-discipline → scoreboard → CHECKPOINT.
"""


@pytest.mark.offline
def test_validate_arc_allows_g2_judgment_when_judgment_required() -> None:
    parsed = parse_checkpoint(_G2_JUDGMENT_BODY)
    verdict = validate_arc_for_admit(
        parsed,
        window_kind="worker",
        admission_mode="autonomous",
        consult_role=None,
        executor_lane="judgment",
        density_triage_lookup=lambda _ref: "judgment_required",
    )
    assert verdict is None


@pytest.mark.offline
def test_evaluate_root_allows_g2_judgment_when_judgment_required(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    turns = [
        _turn(1, "CHECKPOINT wave 1 — G2 densify next", _G2_JUDGMENT_BODY),
    ]
    monkeypatch.setenv("CHARTER_ADMISSION_MODE", "autonomous")
    monkeypatch.setattr(
        "scripts.model_manager.ui.controller.charter_runner.window_terminal_contract.default_density_triage_lookup",
        lambda _ref: "judgment_required",
    )
    decision = evaluate_root("5975", turns, CapStore())
    assert decision.eligible is True
    assert decision.reason != "arc_lane_too_weak"


@pytest.mark.offline
def test_validate_arc_refuses_judgment_required_mechanical_lane() -> None:
    parsed = parse_checkpoint(_G3_MECHANICAL_BODY)
    verdict = validate_arc_for_admit(
        parsed,
        window_kind="worker",
        admission_mode="autonomous",
        consult_role=None,
        executor_lane="implement",
        density_triage_lookup=lambda _ref: "judgment_required",
    )
    assert verdict is not None
    assert verdict.ok is False
    assert verdict.reason == "arc_lane_too_weak"
    assert "r_admit_required" in verdict.fix_hint
    assert "judgment_required" in verdict.fix_hint


@pytest.mark.offline
def test_evaluate_root_refuses_g3_mechanical_when_judgment_required(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    turns = [
        _turn(1, "CHECKPOINT wave 2 — G3 next", _G3_MECHANICAL_BODY),
    ]
    monkeypatch.setattr(
        "scripts.model_manager.ui.controller.charter_runner.window_terminal_contract.default_density_triage_lookup",
        lambda _ref: "judgment_required",
    )
    decision = evaluate_root("5975", turns, CapStore())
    assert decision.eligible is False
    assert decision.reason == "arc_lane_too_weak"


@pytest.mark.offline
@pytest.mark.asyncio
async def test_harvest_skips_second_close_for_same_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from scripts.model_manager.ui.controller.charter_runner import harvest

    hook = AsyncMock()
    monkeypatch.setattr(harvest, "after_window_terminal_harvested", hook)
    monkeypatch.setattr(harvest.window_log, "already_harvested", lambda _r, _w: False)
    monkeypatch.setattr(harvest.window_log, "append_closeout", lambda **_k: None)
    monkeypatch.setattr(
        harvest.bus_client, "fetch_turns", AsyncMock(return_value=[])
    )
    monkeypatch.setattr(
        harvest.bus_client,
        "fetch_thread",
        AsyncMock(return_value={"slug": "arc", "summary": "so what"}),
    )
    monkeypatch.setattr(
        harvest.bus_client, "close_worker_thread", AsyncMock(return_value=None)
    )

    turns = _w2_turns()
    await harvest.harvest_completed_windows("5975", turns)
    monkeypatch.setattr(harvest.window_log, "already_harvested", lambda _r, _w: True)
    await harvest.harvest_completed_windows("5975", turns)
    assert hook.await_count == 1
