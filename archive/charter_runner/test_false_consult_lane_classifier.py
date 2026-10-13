"""False consult lane + layer consult gate classifiers (agent-bus:6486 Path B #1)."""

from __future__ import annotations

import pytest

from scripts.model_manager.ui.controller.charter_runner.admission import (
    CapsView,
    EnvFacts,
    decide,
)
from scripts.model_manager.ui.controller.charter_runner.checkpoint_schema import (
    parse_checkpoint,
)
from scripts.model_manager.ui.controller.charter_runner.gate_lane_classifier import (
    resolve_admit_lane,
    tip_is_consult_shaped,
    tip_is_worker_shaped,
)
from scripts.model_manager.ui.controller.charter_runner.root_ledger import (
    RootLedgerRow,
    RootStatus,
    Transition,
)
from scripts.model_manager.ui.controller.charter_runner.window_exec.materializer_consult import (
    _open_layer_consult_gate,
)

pytestmark = pytest.mark.offline

_G3_DENSIFY_BODY = """\
# CHECKPOINT

## WIP / In-flight
_None this window._

## Next pickup
1. G3 — densify dense spec · todo:layer-widget · executor=cdp/fable

## Steps
1. [x] G1 — architecture verdict · [consult:judgment_gap]
2. [x] G2 — frame · [consult:judgment_gap]
3. [ ] G3 — densify dense spec + Gate-2 close · [judgment]
4. [ ] G4 — merged check · [judgment]
5. [ ] G5 — implement · [implement]

## Frictions
_None this window._

Scoreboard: cortex://notes/system/threads/6489-charter-scoreboard.md
"""

_G1_CONSULT_BODY = """\
# CHECKPOINT

## WIP / In-flight
_None this window._

## Next pickup
1. G1 — architecture · CONSULT_PENDING · consult_role: judgment_gap · executor=cdp/fable

## Steps
1. [ ] G1 — architecture verdict · [consult:judgment_gap]
2. [ ] G2 — frame · [consult:judgment_gap]

## Frictions
_None this window._

Scoreboard: cortex://notes/system/threads/6489-charter-scoreboard.md
"""


def _open_caps() -> CapsView:
    return CapsView(
        allowed=True,
        skip_reason=None,
        stopped_reason=None,
        revise_ok=True,
        revise_reason=None,
    )


def test_g3_densify_is_worker_shaped_not_consult() -> None:
    parsed = parse_checkpoint(_G3_DENSIFY_BODY)
    assert parsed.consult_pending is False
    assert tip_is_worker_shaped(parsed) is True
    assert tip_is_consult_shaped(parsed) is False


def test_consult_pending_is_consult_shaped() -> None:
    parsed = parse_checkpoint(_G1_CONSULT_BODY)
    assert parsed.consult_pending is True
    assert tip_is_consult_shaped(parsed) is True
    assert tip_is_worker_shaped(parsed) is False


def test_open_layer_consult_gate_g3_densify_returns_none() -> None:
    parsed = parse_checkpoint(_G3_DENSIFY_BODY)
    assert _open_layer_consult_gate(parsed) is None


def test_open_layer_consult_gate_g1_consult_returns_g1() -> None:
    parsed = parse_checkpoint(_G1_CONSULT_BODY)
    assert _open_layer_consult_gate(parsed) == "G1"


def test_decide_worker_shaped_autonomous_judgment_admits_worker() -> None:
    row = RootLedgerRow(
        root_id="6489",
        status=RootStatus.IDLE,
        pickup_gid="G3",
        pickup_lane="judgment",
        pickup_executor="cdp/fable",
        attendance="autonomous",
        scoreboard_uri="cortex://notes/system/threads/6489-charter-scoreboard.md",
    )
    transition = decide(
        row,
        EnvFacts(
            substrate_up=True,
            has_wip=False,
            attendance="autonomous",
            tip_executor="cdp/fable",
            worker_shaped_tip=True,
        ),
        _open_caps(),
    )
    assert transition == Transition.ADMIT_WORKER


def test_resolve_admit_lane_false_consult_lane_when_worker_shaped() -> None:
    # Stale open G1 consult annotation while Next-pickup is G3 densify (6489).
    stale_body = _G3_DENSIFY_BODY.replace(
        "1. [x] G1 — architecture verdict · [consult:judgment_gap]",
        "1. [ ] G1 — architecture verdict · [consult:judgment_gap]",
    )
    parsed = parse_checkpoint(stale_body)
    assert tip_is_worker_shaped(parsed) is True
    assert tip_is_consult_shaped(parsed) is False

    class _Log:
        warnings: list[tuple] = []

        def warning(self, msg: str, *args: object) -> None:
            self.warnings.append((msg, args))

    log = _Log()
    window_kind, _mode, _role, _parsed, refuse = resolve_admit_lane(
        parsed,
        default_admission_mode="autonomous",
        root_id="6489",
        log=log,
    )
    assert refuse == "false_consult_lane"
    assert window_kind == "worker"
