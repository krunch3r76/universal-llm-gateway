"""L4 render proof — field_parity line on admit-report consumer surfaces (7119)."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from services.git_integration_worker.cursor_auto.admit_report import build_admit_report_body
from services.git_integration_worker.cursor_auto.continuity_hop import _post_hop_admit_report
from services.git_integration_worker.cursor_auto.field_parity import (
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
    from services.git_integration_worker.cursor_auto.field_parity import FieldParityReport

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
    assert any("allow_self_preempt" in item for item in report.dropped_effect)


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
    assert "field_parity: status=uncomputable(no_row_model)" in posted[0]
    assert "Auto admit-report (hop; no gate)" in posted[0]
