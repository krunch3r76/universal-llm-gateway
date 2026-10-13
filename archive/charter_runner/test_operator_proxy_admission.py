"""Operator-proxy admission mode — attendance wire, packet, and routing."""

from __future__ import annotations

import pytest

from scripts.model_manager.ui.controller.charter_runner.attendance import (
    admission_mode_for_attendance,
    attendance_from_todo_attrs,
)
from scripts.model_manager.ui.controller.charter_runner.checkpoint_schema import (
    parse_checkpoint,
)
from scripts.model_manager.ui.controller.charter_runner.checkpoint_schema import (
    output_format_footer_requirement,
)
from scripts.model_manager.ui.controller.charter_runner.executor_defaults import (
    operator_proxy_host_generate_body,
)
from scripts.model_manager.ui.controller.charter_runner.executor_routing import (
    JUDGMENT_LANE,
    resolve_charter_executor,
)
from scripts.model_manager.ui.controller.charter_runner.window_exec import (
    select_packet,
)
from scripts.model_manager.ui.controller.charter_runner.window_exec import (
    materialize_operator_proxy_packet,
    operator_proxy_subject,
)

_FIXTURE = """\
# CHECKPOINT — continuous operator-proxy drive

## Steps
1. [ ] G1 — kernel rewrite phase 0

## In-flight / WIP
none

## Next pickup
1. G1 — advance kernel rewrite phase 0

## Frictions
_None this window._

## Sidecars
Operator lane: agent-bus:6006

Scoreboard: cortex://notes/system/threads/6036-charter-scoreboard.md

— RESUME (any seat, no command): load agent-bus-discipline body → do not read linearly.
"""

_SIX_BLOCKS = (
    "<scope>",
    "<invariants>",
    "<task_guidance>",
    "<corpus>",
    "<mcp_capabilities>",
    "<output_format>",
)


@pytest.mark.offline
def test_attendance_from_todo_attrs_operator_proxy() -> None:
    assert (
        attendance_from_todo_attrs({"attendance": "operator_proxy"}) == "operator_proxy"
    )


@pytest.mark.offline
def test_admission_mode_for_attendance_operator_proxy() -> None:
    assert admission_mode_for_attendance("operator_proxy") == "operator_proxy"


@pytest.mark.offline
def test_admission_mode_for_attendance_attended_regression() -> None:
    assert admission_mode_for_attendance("attended") == "generate"


@pytest.mark.offline
def test_admission_mode_for_attendance_autonomous_regression() -> None:
    assert admission_mode_for_attendance("autonomous") == "autonomous"


@pytest.mark.offline
def test_operator_proxy_host_generate_body_read_only() -> None:
    body = operator_proxy_host_generate_body(
        root_id="6036",
        window_index=1,
        packet_path="tmp/charter-runner/6036-w1.md",
        subject="test",
        caller_agent="charter-runner",
    )
    assert body["read_only"] is True
    assert body["op"] == "generate"
    assert body["seat"] == "cursor-sdk"


@pytest.mark.offline
def test_materialize_operator_proxy_packet_six_blocks_and_footer() -> None:
    parsed = parse_checkpoint(_FIXTURE)
    packet = materialize_operator_proxy_packet(
        "6036", parsed, scoreboard_uri=None, window_index=1
    )
    for tag in _SIX_BLOCKS:
        assert tag in packet, f"missing {tag}"
    footer_req = output_format_footer_requirement(window_id="charter-6036-w1")
    assert footer_req in packet
    assert "nest_under" in packet
    assert "read_only" in packet or "read-only" in packet.lower()


@pytest.mark.offline
def test_select_packet_operator_proxy() -> None:
    parsed = parse_checkpoint(_FIXTURE)
    packet, subject = select_packet(
        "6036",
        parsed,
        scoreboard_uri=None,
        window_index=1,
        admission_mode="operator_proxy",
    )
    assert "operator-proxy" in subject.lower() or "CDP lane" in subject
    assert "<scope>" in packet
    assert subject == operator_proxy_subject("6036", 1)


@pytest.mark.offline
def test_select_packet_operator_proxy_precedes_arc_lane_layer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC1: operator_proxy ≻ arc_lane — layer lane must not select layer packet."""
    parsed = parse_checkpoint(_FIXTURE)

    def fail_layer(*_a: object, **_k: object) -> str:
        raise AssertionError("materialize_layer_packet must not run under operator_proxy")

    monkeypatch.setattr(
        "scripts.model_manager.ui.controller.charter_runner.window_exec."
        "materializer_autonomous.materialize_layer_packet",
        fail_layer,
    )
    packet, subject = select_packet(
        "6036",
        parsed,
        scoreboard_uri=None,
        window_index=1,
        admission_mode="operator_proxy",
        arc_lane="layer",
    )
    assert "<scope>" in packet
    assert "nest_under" in packet
    assert subject == operator_proxy_subject("6036", 1)


@pytest.mark.offline
def test_resolve_charter_executor_operator_proxy() -> None:
    parsed = parse_checkpoint(_FIXTURE)
    bind = resolve_charter_executor(parsed=parsed, admission_mode="operator_proxy")
    assert bind.lane == JUDGMENT_LANE
    assert bind.reason == "layer_heuristic_refused"
    assert bind.source_ref is None
    assert bind.is_implement is False
