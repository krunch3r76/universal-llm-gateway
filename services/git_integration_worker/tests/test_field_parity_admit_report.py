"""L4 render proof — field_parity line on admit-report consumer surfaces (7119)."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from services.git_integration_worker.cursor_auto.admit_report import (
    build_admit_report_body,
)
from services.git_integration_worker.cursor_auto.continuity_hop import (
    _post_hop_admit_report,
)
from services.git_integration_worker.cursor_auto.field_parity import (
    compute_envelope_parity,
    compute_field_parity_for_job,
    render_field_parity_line,
)
from services.git_integration_worker.cursor_auto.handler import process_job
from services.git_integration_worker.cursor_auto.propagate_admission import (
    admit_propagate_body,
)
from services.git_integration_worker.cursor_auto.queue import AutoJob
from services.git_integration_worker.cursor_auto.wire_map import resolve_desired_effort

_SHORTHAND_PROPAGATE = """\
TYPE: DIRECTIVE
contract: propagate
scope: propagation sync_restart mcp
code_ref: HEAD
safe_window: drain_required
allow_self_preempt: false
effects_expected: propagation row persisted; restart executed or deferred
"""


def test_render_field_parity_line_always_has_status_first() -> None:
    from services.git_integration_worker.cursor_auto.field_parity import (
        FieldParityReport,
    )

    line = render_field_parity_line(
        FieldParityReport(status="ok", scope="propagate_row", consumed=2, unknown=1)
    )
    assert line.startswith("field_parity: status=ok scope=propagate_row")


def test_shorthand_propagate_parity_refuses_dropped_effect_fields() -> None:
    admission = admit_propagate_body(_SHORTHAND_PROPAGATE)
    report = compute_field_parity_for_job(
        body=_SHORTHAND_PROPAGATE,
        contract="propagate",
        propagate_admission=admission,
    )
    assert report.status == "REFUSED"
    assert any("safe_window" in item for item in report.dropped_effect)
    assert not any("allow_self_preempt" in item for item in report.dropped_effect)
    assert admission.rows[0].allow_self_preempt is False


def test_build_admit_report_body_includes_field_parity_line() -> None:
    admission = admit_propagate_body(_SHORTHAND_PROPAGATE)
    parity = compute_field_parity_for_job(
        body=_SHORTHAND_PROPAGATE,
        contract="propagate",
        propagate_admission=admission,
    )
    body = build_admit_report_body(
        model={"requested": "auto", "resolved_model_id": "cursor/composer-2.5", "honored": True},
        effort={"requested": "medium", "resolved_effort": "medium"},
        escalation={"requested": None, "resolved_escalation": None},
        contract="propagate",
        handoff_contract="propagate",
        field_parity_report=parity,
    )
    assert "field_parity: status=REFUSED scope=propagate_row" in body
    assert "dropped_effect=[" in body


def test_process_job_propagate_admit_surfaces_field_parity(monkeypatch: pytest.MonkeyPatch) -> None:
    bus = AsyncMock()
    bus.reply = AsyncMock(return_value=MagicMock(status_code=200, body={}))
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_auto.handler.submit_nested_dispatch",
        AsyncMock(return_value={"ok": False, "error": "stop"}),
    )
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_auto.gate_serialize.sdk_dispatch_gate_stats",
        lambda **_: {"active": 0, "queued": 0, "limit": 1},
    )
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_auto.admit_gates.fetch_thread_status",
        AsyncMock(return_value="active"),
    )
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_auto.admit_gates.fetch_thread_turns",
        AsyncMock(return_value=[]),
    )
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_auto.handler.run_propagation_in_seat",
        AsyncMock(return_value={"disposition": "queued", "summary": "queued"}),
    )

    job = AutoJob(
        job_id="j-parity-render",
        thread_id="7119",
        turn_number=1,
        subject="propagate parity",
        body=_SHORTHAND_PROPAGATE,
        from_agent="web-anthropic",
        to_agent="cursor",
        desired_model="auto",
        desired_effort="medium",
        contract="propagate",
    )
    asyncio.run(process_job(job, bus=bus))
    admit_body = bus.reply.await_args_list[0].kwargs["body"]
    assert "field_parity: status=REFUSED scope=propagate_row" in admit_body
    assert "dropped_effect=[" in admit_body


@pytest.mark.asyncio
async def test_hop_admit_report_surfaces_field_parity_line() -> None:
    posted: list[str] = []

    class _Client:
        async def reply(self, **kwargs):  # type: ignore[no-untyped-def]
            posted.append(str(kwargs.get("body") or ""))
            return MagicMock(status_code=200, body="")

    job = AutoJob(
        job_id="j-hop-parity",
        thread_id="7119",
        turn_number=2,
        subject="hop",
        body="TYPE: CONTINUITY_HANDOFF\n",
        from_agent="web-anthropic",
        to_agent="cursor",
        desired_model="auto",
        desired_effort="xhigh",
        contract="light-bounded",
        continuity_hop=True,
    )
    effort = resolve_desired_effort(job.desired_effort)
    await _post_hop_admit_report(
        job,
        client=_Client(),
        cdp_model="cdp/opus-5",
        effort=effort,
    )
    assert posted
    assert "field_parity: status=ok scope=envelope" in posted[0]
    assert "Auto admit-report (hop; no gate)" in posted[0]


_PROSE_EFFORT_HOP = """\
TYPE: CONTINUITY_HANDOFF
desired_effort: xhigh
Carry the arc forward; the successor should run deep.
"""


@pytest.mark.asyncio
async def test_hop_parity_flags_effort_authored_as_prose() -> None:
    """Instance 3 — ``desired_effort`` in prose never reaches the wire (§AC5)."""
    posted: list[str] = []

    class _Client:
        async def reply(self, **kwargs):  # type: ignore[no-untyped-def]
            posted.append(str(kwargs.get("body") or ""))
            return MagicMock(status_code=200, body="")

    job = AutoJob(
        job_id="j-hop-prose-effort",
        thread_id="7119",
        turn_number=3,
        subject="hop",
        body=_PROSE_EFFORT_HOP,
        from_agent="web-anthropic",
        to_agent="cursor",
        desired_model="auto",
        desired_effort="medium",
        contract="light-bounded",
        continuity_hop=True,
    )
    await _post_hop_admit_report(
        job,
        client=_Client(),
        cdp_model="cdp/opus-5",
        effort=resolve_desired_effort(job.desired_effort),
    )
    assert posted
    assert "field_parity: status=WARN scope=envelope" in posted[0]
    assert "desired_effort(authored=xhigh row=medium)" in posted[0]


def test_envelope_parity_defers_recognised_packet_keys() -> None:
    body = "TYPE: DIRECTIVE\narc: 7119\nauthority: operator\ndensity: judgment\n"
    report = compute_envelope_parity(body, {"desired_effort": "medium"})
    assert report.status == "ok"
    assert report.unknown == 0
    assert report.unknown_tokens == ()
    assert report.deferred == 3
    assert report.deferred_tokens == ("arc", "authority", "density")


def test_envelope_parity_warns_on_unrecognised_token() -> None:
    body = "TYPE: DIRECTIVE\narcx: 7119\ndensiy: judgment\n"
    report = compute_envelope_parity(body, {"desired_effort": "medium"})
    assert report.status == "WARN"
    assert report.unknown == 2
    assert report.unknown_tokens == ("arcx", "densiy")
    assert report.deferred == 0


def test_envelope_parity_defers_standard_directive_vocab() -> None:
    body = (
        "TYPE: DIRECTIVE\n"
        "arc: 7190\n"
        "assumed_state: manage is stale\n"
        "authority: cursor-sdk\n"
        "budget: <=3\n"
        "density: dense\n"
        "evidence_required: /proc/environ\n"
        "files_expected: you determine\n"
        "vision: check inheritance not ancestry\n"
    )
    report = compute_envelope_parity(body, {"desired_effort": "high"})
    assert report.status == "ok"
    assert report.unknown == 0
    assert report.deferred == 8
    rendered = render_field_parity_line(report)
    assert "status=ok" in rendered
    assert "deferred=8" in rendered
    assert "unknown=0" in rendered
    assert "deferred=[arc, assumed_state, authority, budget, density, " in rendered


def test_envelope_parity_defers_idea_commission_keys() -> None:
    """idea/kind/peer_disclosure are commission prose; from_lane stays unknown."""
    body = (
        "TYPE: DIRECTIVE\n"
        "idea: residual sweep\n"
        "kind: investigate+fix\n"
        "peer_disclosure: agent-bus:9530\n"
        "from_lane: 9541\n"
    )
    report = compute_envelope_parity(body, {"desired_effort": "medium"})
    assert report.status == "WARN"
    assert report.unknown_tokens == ("from_lane",)
    assert report.deferred_tokens == ("idea", "kind", "peer_disclosure")


def test_envelope_parity_ok_when_prose_agrees_with_live_envelope() -> None:
    body = "TYPE: CONTINUITY_HANDOFF\ndesired_effort: medium\n"
    report = compute_envelope_parity(body, {"desired_effort": "medium"})
    assert report.status == "ok"
    assert report.consumed == 1
    assert report.dropped_effect == ()


def test_envelope_parity_ignores_fenced_and_backticked_authorship() -> None:
    body = (
        "TYPE: CONTINUITY_HANDOFF\n"
        "I wrote `desired_effort: xhigh` deliberately\n"
        "```\ndesired_effort: xhigh\n```\n"
    )
    report = compute_envelope_parity(body, {"desired_effort": "medium"})
    assert report.status == "ok"
    assert report.dropped_effect == ()


def test_answer_contract_stays_out_of_parity_scope() -> None:
    report = compute_field_parity_for_job(
        body="desired_effort: xhigh\n",
        contract="answer",
        envelope={"desired_effort": "medium"},
    )
    assert report.status == "uncomputable(no_row_model)"
    assert report.scope == "answer"
