"""Layer-native autonomous packet fixtures (agent-bus:6467 G3)."""

from __future__ import annotations

import hashlib
import re

import pytest

from scripts.model_manager.ui.controller.charter_runner.admission import (
    CapsView,
    EnvFacts,
    decide,
    layer_independence_ok,
    layer_independence_unproven,
)
from scripts.model_manager.ui.controller.charter_runner.attendance import (
    arc_lane_from_todo_attrs,
)
from scripts.model_manager.ui.controller.charter_runner.checkpoint_schema import (
    parse_checkpoint,
)
from scripts.model_manager.ui.controller.charter_runner.executor_routing import (
    resolve_charter_executor,
)
from scripts.model_manager.ui.controller.charter_runner.gate_lane_classifier import (
    classify,
    parse_gate_rows,
)
from scripts.model_manager.ui.controller.charter_runner.root_ledger import (
    RootLedgerRow,
    RootStatus,
)
from scripts.model_manager.ui.controller.charter_runner.window_exec import (
    materialize_autonomous_packet,
    materialize_consult_packet,
    select_packet,
)

pytestmark = pytest.mark.offline

_LAYER_STEPS = """\
1. [ ] G1 — architecture verdict + target shape · [consult:judgment_gap]
2. [ ] G2 — frame (Opus → densifier instructions, ≤120 lines) · [consult:judgment_gap]
3. [ ] G3 — densify dense spec + Gate-2 close · [judgment]
4. [ ] G4 — merged check · [judgment]
5. [ ] G5 — implement (Composer, source_ref) · [implement]
6. [ ] G6 — verify + close (gates · ACs · docstrings) · [inline]
"""

_LAYER_BODY = f"""\
# CHECKPOINT

## WIP / In-flight
_None this window._

## Next pickup
1. G3 — densify dense spec · todo:layer-widget · executor_lane: judgment

## Steps
{_LAYER_STEPS}

## Frictions
_None this window._

## Sidecars
_None this window._

Scoreboard: cortex://notes/system/threads/6467-charter-scoreboard.md

— RESUME (any seat, no command): load agent-bus-discipline body → do not read linearly.
"""

_CLOSED_LAYER_BODY = """\
# CHECKPOINT

## WIP / In-flight
_None this window._

## Next pickup
1. G5 — implement bind · todo:layer-widget · detent=closed · executor_lane: implement

## Steps
1. [x] G1 — architecture
2. [x] G2 — frame
3. [x] G3 — densify
4. [x] G4 — check
5. [ ] G5 — implement · [implement]
6. [ ] G6 — verify · [inline]

## Frictions
_None this window._

## Sidecars
_None this window._

Scoreboard: cortex://notes/system/threads/6467-charter-scoreboard.md

— RESUME (any seat, no command): load agent-bus-discipline body → do not read linearly.
"""

_PATH_SIM_BODY = """\
# CHECKPOINT

## WIP / In-flight
_None this window._

## Next pickup
1. G2 — A + Gate-2 · todo:path-sim-widget · executor_lane: judgment

## Steps
1. [x] G1-Q — L0 question table
2. [ ] G2 — A + Gate-2 dense spec + implement_ready
3. [ ] G3 — R-admit · [consult:r_admit]

## Frictions
_None this window._

## Sidecars
_None this window._

Scoreboard: cortex://notes/system/threads/6467-charter-scoreboard.md

— RESUME (any seat, no command): load agent-bus-discipline body → do not read linearly.
"""

_G1_CONSULT_BODY = """\
# CHECKPOINT

## WIP / In-flight
_None this window._

## Next pickup
1. G1 — architecture · CONSULT_PENDING · consult_role: judgment_gap · executor_lane: judgment

## Steps
1. [ ] G1 — architecture verdict · [consult:judgment_gap]
2. [ ] G2 — frame · [consult:judgment_gap]

## Frictions
_None this window._

## Sidecars
_None this window._

Scoreboard: cortex://notes/system/threads/6467-charter-scoreboard.md

— RESUME (any seat, no command): load agent-bus-discipline body → do not read linearly.
"""

_PROVENANCE_BODY = """
## Consult provenance
- consult_thread: agent-bus:7001
- verdict: ADMIT
- consultant_model: claude-fable-5-1
- consultant_effort: high
- consultant_substrate: web-anthropic
- gate_id: G2
"""

_G5_BLOCK_BODY = """\
# CHECKPOINT

## WIP / In-flight
_None this window._

## Next pickup
1. G5 — implement · todo:layer-widget · executor_lane: implement

## Steps
1. [x] G1 — architecture
2. [x] G2 — frame
3. [x] G3 — densify
4. [x] G4 — check
5. [ ] G5 — implement · [implement]

## Anchor
- Todo: todo:layer-widget

## Frictions
_None this window._

## Sidecars
_None this window._

Scoreboard: cortex://notes/system/threads/6467-charter-scoreboard.md

— RESUME (any seat, no command): load agent-bus-discipline body → do not read linearly.
"""


def _norm_hash(packet: str) -> str:
    out = re.sub(r"\bwindow \d+\b", "window", packet, flags=re.IGNORECASE)
    return hashlib.sha256(out.encode()).hexdigest()


def test_layer_packet_selected_for_arc_lane_layer() -> None:
    parsed = parse_checkpoint(_LAYER_BODY)
    packet, subject = select_packet(
        "6467",
        parsed,
        scoreboard_uri="cortex://notes/system/threads/6467-charter-scoreboard.md",
        window_index=1,
        admission_mode="autonomous",
        arc_lane="layer",
    )
    assert "layer autonomous arc" in subject
    assert "abstraction-layering" in packet


def test_layer_packet_has_no_path_sim_surface() -> None:
    parsed = parse_checkpoint(_LAYER_BODY)
    packet, _ = select_packet(
        "6467",
        parsed,
        scoreboard_uri=None,
        window_index=1,
        admission_mode="autonomous",
        arc_lane="layer",
    )
    lowered = packet.lower()
    assert "path-sim" not in lowered
    assert "r-admit" not in lowered
    assert "r_admit" not in lowered


def test_layer_closed_detent_is_mechanical_leg() -> None:
    parsed = parse_checkpoint(_CLOSED_LAYER_BODY)
    packet, subject = select_packet(
        "6467",
        parsed,
        scoreboard_uri=None,
        window_index=2,
        admission_mode="autonomous",
        arc_lane="layer",
    )
    assert "mechanical leg" in subject or "mechanical leg" in packet
    assert "G5 Implement + G6 Verify" in packet
    assert "Do NOT fire G3 R-admit" not in packet


def test_path_sim_autonomous_packet_unchanged() -> None:
    parsed = parse_checkpoint(_PATH_SIM_BODY)
    board = "cortex://notes/system/threads/6467-charter-scoreboard.md"
    direct = materialize_autonomous_packet(
        "6467", parsed, window_index=3, scoreboard_uri=board
    )
    selected, _ = select_packet(
        "6467",
        parsed,
        scoreboard_uri=board,
        window_index=3,
        admission_mode="autonomous",
        arc_lane="path_sim",
    )
    assert _norm_hash(direct) == _norm_hash(selected)


def test_executor_routing_refuses_g4_heuristic_under_layer() -> None:
    pickup = "G4 — merged check · todo:layer-widget"
    parsed = parse_checkpoint(
        f"# CHECKPOINT\n\n## Next pickup\n1. {pickup}\n\n## Anchor\n- Todo: todo:layer-widget\n"
    )
    for admission_mode in ("autonomous", "operator_proxy"):
        layer_bind = resolve_charter_executor(
            parsed=parsed, admission_mode=admission_mode, arc_lane="layer"
        )
        assert (layer_bind.lane, layer_bind.reason) == (
            "judgment",
            "layer_heuristic_refused",
        ), admission_mode
    path_bind = resolve_charter_executor(
        parsed=parsed, admission_mode="autonomous", arc_lane="path_sim"
    )
    assert path_bind.reason == "heuristic_g4"
    proxy_path_bind = resolve_charter_executor(
        parsed=parsed, admission_mode="operator_proxy", arc_lane="path_sim"
    )
    assert proxy_path_bind.reason == "heuristic_g4"


def test_layer_steps_template_classifies() -> None:
    parsed = parse_checkpoint(f"# CHECKPOINT\n\n## Steps\n{_LAYER_STEPS}\n")
    rows = parse_gate_rows(parsed.steps)
    kinds: list[tuple[str, str | None]] = []
    for row in rows:
        req = classify([row])
        if req is None:
            continue
        kinds.append((req.kind, req.role))
    assert kinds == [
        ("consult", "judgment_gap"),
        ("consult", "judgment_gap"),
        ("worker", None),
        ("worker", None),
        ("worker", None),
        ("worker", None),
    ]


def test_arc_lane_from_todo_attrs() -> None:
    assert arc_lane_from_todo_attrs({"arc_lane": "layer"}) == "layer"
    assert arc_lane_from_todo_attrs({}) == "layer"
    assert arc_lane_from_todo_attrs(None) == "layer"
    assert arc_lane_from_todo_attrs({"arc_lane": " LAYER "}) == "layer"
    assert arc_lane_from_todo_attrs({"arc_lane": "path_sim"}) == "path_sim"
    assert arc_lane_from_todo_attrs({"arc_lane": "garbage"}) == "path_sim"


def test_autonomous_unset_arc_lane_selects_layer() -> None:
    """Unset arc_lane defaults layer — same packet path as explicit arc_lane=layer."""
    parsed = parse_checkpoint(_LAYER_BODY)
    board = "cortex://notes/system/threads/6467-charter-scoreboard.md"
    explicit, explicit_subject = select_packet(
        "6467",
        parsed,
        scoreboard_uri=board,
        window_index=1,
        admission_mode="autonomous",
        arc_lane="layer",
    )
    default, default_subject = select_packet(
        "6467",
        parsed,
        scoreboard_uri=board,
        window_index=1,
        admission_mode="autonomous",
    )
    assert "layer autonomous arc" in default_subject
    assert "abstraction-layering" in default
    assert _norm_hash(explicit) == _norm_hash(default)
    assert explicit_subject == default_subject


def test_layer_g1_not_blocked_by_future_g5_step() -> None:
    """Birth tip with open G5 in Steps must not false-positive independence (dogfood 6489)."""
    parsed = parse_checkpoint(_LAYER_BODY)
    assert not layer_independence_unproven(
        arc_lane="layer",
        attendance="autonomous",
        parsed=parsed,
        checkpoint_body=_LAYER_BODY,
        pickup_lane="consult",
    )


def test_resolve_consult_role_sniffs_steps_judgment_gap() -> None:
    """Layer G1 Steps ``[consult:judgment_gap]`` must win over consult→r_admit default."""
    from scripts.model_manager.ui.controller.charter_runner.consult_lane import (
        resolve_consult_role,
    )

    parsed = parse_checkpoint(_LAYER_BODY.replace(
        "1. G3 — densify dense spec · todo:layer-widget · executor_lane: judgment",
        "1. G1 — architecture · todo:layer-widget · executor_lane: judgment",
    ))
    # Force Steps-first open annotate to G1 judgment_gap (already in _LAYER_STEPS)
    row = RootLedgerRow(
        root_id="6489",
        status=RootStatus.IDLE,
        pickup_gid="G1",
        pickup_lane="consult",
        pickup_executor="cdp/fable",
        attendance="autonomous",
        scoreboard_uri="cortex://notes/system/threads/layer-native-dogfood-g4-scoreboard.md",
    )
    assert resolve_consult_role(row, parsed) == "judgment_gap"


def test_layer_g5_blocked_without_independence_evidence() -> None:
    parsed = parse_checkpoint(_G5_BLOCK_BODY)
    assert layer_independence_unproven(
        arc_lane="layer",
        attendance="autonomous",
        parsed=parsed,
        checkpoint_body=_G5_BLOCK_BODY,
        pickup_lane="mechanical",
    )
    row = RootLedgerRow(
        root_id="6467",
        status=RootStatus.IDLE,
        pickup_gid="G5",
        pickup_lane="mechanical",
        pickup_executor=None,
        attendance="autonomous",
        scoreboard_uri="cortex://notes/system/threads/6467-charter-scoreboard.md",
    )
    transition = decide(
        row,
        EnvFacts(
            substrate_up=True,
            has_wip=False,
            attendance="autonomous",
            arc_lane="layer",
            layer_independence_block=True,
        ),
        CapsView(
            allowed=True,
            skip_reason=None,
            stopped_reason=None,
            revise_ok=True,
            revise_reason=None,
        ),
    )
    assert transition.value == "BLOCK"
    provenance_body = _G5_BLOCK_BODY + _PROVENANCE_BODY + (
        "\nG3 densifier consultant_model: grok-4.6\n"
        "G4 check consultant_model: gpt-5.6-terra\n"
    )
    assert layer_independence_ok(parsed=parsed, checkpoint_body=provenance_body).ok
    assert not layer_independence_unproven(
        arc_lane="layer",
        attendance="autonomous",
        parsed=parsed,
        checkpoint_body=provenance_body,
        pickup_lane="mechanical",
    )


def test_layer_consult_packet_routes_g1_to_fable() -> None:
    parsed = parse_checkpoint(_G1_CONSULT_BODY)
    packet = materialize_consult_packet(
        "6467",
        parsed,
        scoreboard_uri="cortex://notes/system/threads/6467-charter-scoreboard.md",
        window_index=1,
        arc_lane="layer",
    )
    assert "cdp/fable" in packet
    assert "L0" not in packet


def test_layer_g5_operator_proxy_admitted_without_provenance() -> None:
    """F-R3: operator_proxy branch (C); autonomous blocks on same root."""
    parsed = parse_checkpoint(_G5_BLOCK_BODY)
    assert not layer_independence_unproven(
        arc_lane="layer",
        attendance="operator_proxy",
        parsed=parsed,
        checkpoint_body=_G5_BLOCK_BODY,
        pickup_lane="mechanical",
    )
    verdict = layer_independence_ok(
        parsed=parsed,
        checkpoint_body=_G5_BLOCK_BODY,
        attendance="operator_proxy",
    )
    assert verdict.ok
    assert verdict.structural_reason == "operator_proxy_attends"
    assert verdict.branch_b_source == "g4_unpinned"
    assert layer_independence_unproven(
        arc_lane="layer",
        attendance="autonomous",
        parsed=parsed,
        checkpoint_body=_G5_BLOCK_BODY,
        pickup_lane="mechanical",
    )


def test_layer_independence_reason_consult_provenance() -> None:
    parsed = parse_checkpoint(_G5_BLOCK_BODY)
    body = _G5_BLOCK_BODY + _PROVENANCE_BODY + (
        "\nG3 densifier consultant_model: grok-4.6\n"
        "G4 check consultant_model: gpt-5.6-terra\n"
    )
    verdict = layer_independence_ok(
        parsed=parsed,
        checkpoint_body=body,
        attendance="autonomous",
    )
    assert verdict.ok
    assert verdict.structural_reason == "consult_provenance"
    assert verdict.branch_b_source == "checkpoint_provenance"


def test_layer_independence_reason_derived_from_architecture() -> None:
    parsed = parse_checkpoint(_G5_BLOCK_BODY)
    body = _G5_BLOCK_BODY + (
        "\nderived_from: architecture-consult-doc\n"
        "G4 check consultant_model: gpt-5.6-terra\n"
    )
    verdict = layer_independence_ok(
        parsed=parsed,
        checkpoint_body=body,
        attendance="autonomous",
    )
    assert verdict.ok
    assert verdict.structural_reason == "derived_from_architecture"
    assert verdict.branch_b_source == "checkpoint_provenance"


def test_consult_provenance_md_round_trips_model_and_effort() -> None:
    from scripts.model_manager.charter_control.r_verdict_gate import (
        ConsultProvenance,
        format_consult_provenance_md,
    )
    from scripts.model_manager.ui.controller.charter_runner.admission.decide import (
        _provenance_fields,
    )

    prov = ConsultProvenance(
        consult_thread="agent-bus:99",
        verdict="ADMIT",
        consultant_model="claude-fable-5-1",
        consultant_effort="high",
        consultant_substrate="web-anthropic",
    )
    md = format_consult_provenance_md(prov)
    fields = _provenance_fields(md)
    assert fields["consultant_model"] == "claude-fable-5-1"
    assert fields["consultant_effort"] == "high"
    assert fields["consultant_substrate"] == "web-anthropic"
    assert "consultant_family" not in fields


def test_layer_independence_same_model_g3_g4_blocks() -> None:
    parsed = parse_checkpoint(_G5_BLOCK_BODY)
    body = _G5_BLOCK_BODY + _PROVENANCE_BODY + (
        "\nG3 densifier consultant_model: grok-4.6\n"
        "G4 check consultant_model: grok-4.6\n"
    )
    verdict = layer_independence_ok(
        parsed=parsed,
        checkpoint_body=body,
        attendance="autonomous",
    )
    assert not verdict.ok
    assert verdict.branch_b_source is None

    body_rung_diverse = _G5_BLOCK_BODY + """
## Consult provenance
- consult_thread: agent-bus:7001
- verdict: ADMIT
- consultant_model: grok-4.6
- consultant_effort: xhigh
- consultant_substrate: web-anthropic
- gate_id: G3
""" + "\nG4 check consultant_model: grok-4.6-high\n"
    verdict_rung = layer_independence_ok(
        parsed=parsed,
        checkpoint_body=body_rung_diverse,
        attendance="autonomous",
    )
    assert verdict_rung.ok
    assert verdict_rung.branch_b_source == "checkpoint_provenance"


def test_layer_independence_autonomous_blocks_without_structural_reason() -> None:
    parsed = parse_checkpoint(_G5_BLOCK_BODY)
    verdict = layer_independence_ok(
        parsed=parsed,
        checkpoint_body=_G5_BLOCK_BODY,
        attendance="autonomous",
    )
    assert not verdict.ok
    assert verdict.structural_reason is None
    assert layer_independence_unproven(
        arc_lane="layer",
        attendance="autonomous",
        parsed=parsed,
        checkpoint_body=_G5_BLOCK_BODY,
        pickup_lane="mechanical",
    )


def test_layer_g4_check_identity_diverse_seat_bind() -> None:
    from scripts.model_manager.ui.controller.charter_runner.window_exec.materializer_layer import (
        LAYER_G3_SEAT,
        LAYER_G4_SEAT,
        layer_g4_check_identity_diverse,
    )

    assert layer_g4_check_identity_diverse()
    assert not layer_g4_check_identity_diverse(g3_seat=LAYER_G3_SEAT, g4_seat=LAYER_G3_SEAT)
    assert layer_g4_check_identity_diverse(
        g3_seat="cursor/grok-4.6",
        g3_knobs={"effort": "xhigh"},
        g4_seat="cursor/grok-4.6-high",
    )
    assert layer_g4_check_identity_diverse(
        g3_seat=LAYER_G3_SEAT, g4_seat=LAYER_G4_SEAT
    )
    assert not layer_g4_check_identity_diverse(
        g3_seat="cursor/claude-opus-5", g4_seat="cdp/opus-5"
    )
