"""Gate lane classifier — Steps annotation routing (agent-bus:5993 / 6036)."""

from __future__ import annotations

import pytest

from scripts.model_manager.ui.controller.charter_runner.checkpoint_schema import (
    Step,
    parse_checkpoint,
)
from scripts.model_manager.ui.controller.charter_runner.admission import (
    evaluate_root,
)
from scripts.model_manager.ui.controller.charter_runner.gate_lane_classifier import (
    GateRow,
    classify,
    parse_gate_rows,
    resolve_admit_lane,
)

_FIXTURE_5993 = """\
# CHECKPOINT — G3 R-admit (no CONSULT_PENDING cross-check)

## Steps
1. [x] G2 — dense spec
2. [ ] G3 — R-admit · [consult:r_admit]

## In-flight / WIP
none

## Next pickup
1. G3 — R-admit · todo:foo-bar · executor_lane: judgment

## Frictions
_None this window._

## Sidecars
_None this window._

Scoreboard: cortex://notes/system/threads/5993-charter-scoreboard.md

— RESUME (any seat, no command): load agent-bus-discipline body → do not read linearly.
"""

_FIXTURE_G4_IMPLEMENT = """\
# CHECKPOINT — G4 implement

## Steps
1. [x] G3 — R-admit done
2. [ ] G4 — implement · [implement]

## In-flight / WIP
none

## Next pickup
1. G4 — implement · todo:foo-bar · executor_lane: implement

## Frictions
_None this window._

## Sidecars
_None this window._

Scoreboard: cortex://notes/system/threads/5993-charter-scoreboard.md

— RESUME (any seat, no command): load agent-bus-discipline body → do not read linearly.
"""

_FIXTURE_LEGACY = """\
# CHECKPOINT — legacy no annotations

## Steps
1. [x] G2
2. [ ] G3 — R-admit

## In-flight / WIP
none

## Next pickup
1. CONSULT_PENDING — G3 R-admit · consult_role: r_admit

## Frictions
_None this window._

## Sidecars
_None this window._

Scoreboard: cortex://notes/system/threads/5993-charter-scoreboard.md

— RESUME (any seat, no command): load agent-bus-discipline body → do not read linearly.
"""


def _turn(n: int, subject: str, body: str) -> dict:
    return {"turn_number": n, "subject": subject, "body": body}


@pytest.mark.offline
def test_classify_g3_consult_r_admit_without_consult_pending() -> None:
    parsed = parse_checkpoint(_FIXTURE_5993)
    assert parsed.consult_pending is False
    rows = parse_gate_rows(parsed.steps)
    req = classify(rows)
    assert req is not None
    assert req.kind == "consult"
    assert req.role == "r_admit"
    assert req.gate_id == "G3"


@pytest.mark.offline
def test_classify_g4_implement_worker() -> None:
    parsed = parse_checkpoint(_FIXTURE_G4_IMPLEMENT)
    req = classify(parse_gate_rows(parsed.steps))
    assert req is not None
    assert req.kind == "worker"
    assert req.role is None
    assert req.gate_id == "G4"


@pytest.mark.offline
def test_classify_no_annotations_legacy_none() -> None:
    parsed = parse_checkpoint(_FIXTURE_LEGACY)
    assert classify(parse_gate_rows(parsed.steps)) is None


@pytest.mark.offline
def test_parse_gate_rows_from_steps() -> None:
    steps = [
        Step(1, "G2 done", "done"),
        Step(2, "G3 — R-admit · [consult:r_admit]", "pending"),
    ]
    rows = parse_gate_rows(steps)
    assert rows == [
        GateRow(1, "done", "G2 done", None),
        GateRow(2, "pending", "G3 — R-admit · [consult:r_admit]", "consult:r_admit"),
    ]


@pytest.mark.offline
def test_eligibility_routes_annotated_g3_to_consult_without_consult_pending() -> None:
    from scripts.model_manager.ui.controller.charter_runner.admission import CapStore

    decision = evaluate_root(
        "5993",
        [_turn(3, "CHECKPOINT", _FIXTURE_5993)],
        CapStore(),
    )
    assert decision.eligible is True
    assert decision.window_kind == "consult"
    assert decision.parsed is not None
    assert decision.parsed.consult_role == "r_admit"


@pytest.mark.offline
def test_resolve_admit_lane_classifier_authoritative() -> None:
    from universal_logging import get_logger

    parsed = parse_checkpoint(_FIXTURE_5993)
    window_kind, admission_mode, role, out, refuse = resolve_admit_lane(
        parsed,
        default_admission_mode="autonomous",
        root_id="5993",
        log=get_logger(__name__),
    )
    assert refuse is None
    assert window_kind == "consult"
    assert admission_mode == "consult"
    assert role == "r_admit"
    assert out.consult_role == "r_admit"
