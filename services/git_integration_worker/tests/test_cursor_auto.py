"""Unit tests for Cursor Auto wire map + gate serialize + liveness registry."""

from __future__ import annotations

import time
from unittest.mock import patch

import pytest

from services.git_integration_worker.cursor_auto.gate_serialize import (
    NESTED_IN_SEAT_REASON,
    plan_nested_dispatch,
    prefer_dispatch_over_park,
    should_run_in_seat,
)
from services.git_integration_worker.cursor_auto.liveness import AutoLivenessRegistry
from services.git_integration_worker.cursor_auto.wire_map import (
    admit_model_pin_flags,
    assess_model_pin,
    resolve_contract_disposition,
    resolve_desired_effort,
    resolve_desired_model,
    resolve_handoff_contract,
)


def test_wire_map_auto_by_contract():
    assert (
        resolve_desired_model("auto", contract="investigate")["resolved_model_id"]
        == "cursor/grok-4.5"
    )
    assert (
        resolve_desired_model("opus-5")["resolved_model_id"]
        == "cursor/claude-opus-5"
    )
    effort = resolve_desired_effort("bogus")
    assert effort["clamped"] and effort["resolved_effort"] == "medium"
    assert resolve_contract_disposition("implement")["disposition_hint"] == (
        "dispatched-and-relayed"
    )
    assert resolve_handoff_contract("implement") == "pure-mechanical"
    assert resolve_handoff_contract("investigate") == "light-bounded"


def test_wire_map_accepts_cursor_prefixed_model():
    assert (
        resolve_desired_model("cursor/grok-4.5")["resolved_model_id"]
        == "cursor/grok-4.5"
    )
    assert resolve_desired_model("cursor/grok-4.5")["honored"] is True
    assert (
        resolve_desired_model("cursor/composer-2.5")["resolved_model_id"]
        == "cursor/composer-2.5"
    )


def test_wire_map_rejects_unknown_model():
    out = resolve_desired_model("cursor/claude-sonnet-4")
    assert out["rejected"] is True
    assert out["honored"] is False
    assert out["resolved_model_id"] is None
    assert "bindable" in out["notes"]


def test_assess_model_pin_blocks_body_desired_model():
    model, block = assess_model_pin(
        "grok-4.5",
        contract="investigate",
        body="TYPE: DIRECTIVE\ndesired_model: grok-4.5\n",
    )
    assert block is not None
    assert "wire-only" in block
    assert model["resolved_model_id"] == "cursor/grok-4.5"


def test_admit_model_pin_flags_surfaces_effort_clamp():
    model = resolve_desired_model("grok-4.5")
    effort = resolve_desired_effort("bogus")
    flags = admit_model_pin_flags(model, effort)
    assert any("effort_clamped" in flag for flag in flags)


def test_process_job_blocks_unknown_model_pin(monkeypatch):
    import asyncio
    import json
    from unittest.mock import AsyncMock, MagicMock

    from services.git_integration_worker.cursor_auto.handler import process_job
    from services.git_integration_worker.cursor_auto.queue import AutoJob

    bus = AsyncMock()
    bus.reply = AsyncMock(return_value=MagicMock(status_code=200, body={}))

    job = AutoJob(
        job_id="j-bad-model",
        thread_id="6654",
        turn_number=1,
        subject="bad model",
        body="TYPE: DIRECTIVE\ndensity: dense\n## Scope\nfoo\nvision: test\n",
        from_agent="web-anthropic",
        to_agent="cursor",
        desired_model="cursor/claude-sonnet-4",
        desired_effort="medium",
        contract="investigate",
    )

    result = asyncio.run(process_job(job, bus=bus))
    assert result["terminal_status"] == "status:blocked"
    payload = json.loads(bus.reply.await_args.kwargs["body"])
    assert payload["reason"] == "model_pin_refused"
    assert "bindable" in payload["summary"]


def test_process_job_admit_surfaces_model_honored(monkeypatch):
    import asyncio
    from unittest.mock import AsyncMock, MagicMock

    from services.git_integration_worker.cursor_auto.handler import process_job
    from services.git_integration_worker.cursor_auto.queue import AutoJob

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
        "services.git_integration_worker.cursor_auto.admit_gates.fetch_thread_turns",
        AsyncMock(return_value=[]),
    )

    job = AutoJob(
        job_id="j-admit-flags",
        thread_id="6654",
        turn_number=1,
        subject="admit flags",
        body="TYPE: DIRECTIVE\ndensity: dense\n## Scope\nfoo\nvision: test\n",
        from_agent="web-anthropic",
        to_agent="cursor",
        desired_model="cursor/grok-4.5",
        desired_effort="bogus",
        contract="investigate",
    )

    asyncio.run(process_job(job, bus=bus))
    admit_call = bus.reply.await_args_list[0]
    assert admit_call.kwargs["subject"].startswith("status:admitted")
    admit_body = admit_call.kwargs["body"]
    assert "model_honored=True" in admit_body
    assert "effort_clamped" in admit_body


def test_wire_map_effort_xhigh_and_aliases():
    assert resolve_desired_effort("xhigh") == {
        "requested": "xhigh",
        "resolved_effort": "xhigh",
        "clamped": False,
        "notes": "honored",
    }
    for alias in ("extra", "extra-high", "extra high", "Extra High"):
        out = resolve_desired_effort(alias)
        assert out["resolved_effort"] == "xhigh"
        assert out["clamped"] is False
        assert "normalized" in out["notes"]
    assert resolve_desired_effort("max")["resolved_effort"] == "max"


def test_liveness_registry_ttl():
    reg = AutoLivenessRegistry(heartbeat_ttl_s=0.01)
    assert not reg.is_live()
    reg.register("h1")
    assert reg.is_live()
    time.sleep(0.02)
    assert not reg.is_live()


def test_gate_serialize_nest_park_when_at_capacity():
    with patch(
        "services.git_integration_worker.cursor_auto.gate_serialize.sdk_dispatch_gate_stats",
        return_value={"active": 1, "queued": 0, "limit": 1},
    ):
        assert should_run_in_seat() is True
        plan = plan_nested_dispatch(work_bounded=False)
        assert plan["action"] == "nest_park"
        assert "park" in plan["reason"]


def test_gate_serialize_in_seat_when_park_unavailable():
    with patch(
        "services.git_integration_worker.cursor_auto.gate_serialize.sdk_dispatch_gate_stats",
        return_value={"active": 1, "queued": 0, "limit": 1},
    ):
        plan = plan_nested_dispatch(work_bounded=False, park_available=False)
        assert plan["action"] == "in_seat"
        assert "park_unavailable" in plan["reason"]


def test_nested_in_seat_reason_token():
    assert NESTED_IN_SEAT_REASON == "nested_in_seat_unsupported"


def test_gate_serialize_dispatch_when_capacity():
    with patch(
        "services.git_integration_worker.cursor_auto.gate_serialize.sdk_dispatch_gate_stats",
        return_value={"active": 0, "queued": 0, "limit": 1},
    ):
        assert should_run_in_seat() is False
        plan = plan_nested_dispatch(work_bounded=False)
        assert plan["action"] == "dispatch_now"


def test_gate_serialize_bounded_dispatches_when_gate_free():
    with patch(
        "services.git_integration_worker.cursor_auto.gate_serialize.sdk_dispatch_gate_stats",
        return_value={"active": 0, "queued": 0, "limit": 1},
    ):
        plan = plan_nested_dispatch(work_bounded=True)
        assert plan["action"] == "dispatch_now"
        assert plan["reason"] == "gate_has_capacity"


def test_gate_serialize_bounded_nest_park_when_gate_held_not_in_seat():
    """AC-5967: sparse/bounded at capacity with holder must not plan in_seat."""
    gate_stats = {"active": 1, "queued": 0, "limit": 1}
    with patch(
        "services.git_integration_worker.cursor_auto.gate_serialize.sdk_dispatch_gate_stats",
        return_value=gate_stats,
    ):
        plan = plan_nested_dispatch(work_bounded=True)
        assert plan["action"] == "nest_park"
        assert plan["reason"] == "gate_at_capacity_prefer_park"

    with patch(
        "services.git_integration_worker.cursor_auto.gate_serialize.sdk_dispatch_gate_stats",
        return_value=gate_stats,
    ), patch(
        "services.git_integration_worker.cursor_dispatch_ledger.CursorDispatchLedger.instance"
    ) as ledger_cls:
        ledger_cls.return_value.lease_snapshot.return_value = {
            "holder_dispatch_id": "peer-holder",
        }
        plan = prefer_dispatch_over_park(plan, work_bounded=True)
        assert plan["action"] == "nest_park"
        assert plan["reason"] == "gate_at_capacity_prefer_park"


def test_gate_serialize_bounded_holderless_prefers_dispatch_at_capacity():
    with patch(
        "services.git_integration_worker.cursor_auto.gate_serialize.sdk_dispatch_gate_stats",
        return_value={"active": 1, "queued": 0, "limit": 1},
    ), patch(
        "services.git_integration_worker.cursor_dispatch_ledger.CursorDispatchLedger.instance"
    ) as ledger_cls:
        ledger_cls.return_value.lease_snapshot.return_value = {
            "holder_dispatch_id": None,
        }
        plan = prefer_dispatch_over_park(
            plan_nested_dispatch(work_bounded=True),
            work_bounded=True,
        )
        assert plan["action"] == "dispatch_now"
        assert plan["reason"] == "holderless_bounded_prefer_dispatch"


def test_directive_parse_require_attended_variants():
    from services.git_integration_worker.cursor_auto.directive import parse_request_body

    base = "TYPE: DIRECTIVE\ndensity: dense\n"
    assert parse_request_body(base + "require_attended: true") is not None
    assert parse_request_body(base + "require_attended: true").require_attended
    assert parse_request_body(base + "  require_attended: True").require_attended
    assert parse_request_body(base + "- require_attended: true").require_attended
    assert parse_request_body(base + "require_attended: false") is not None
    assert not parse_request_body(base + "require_attended: false").require_attended
    assert parse_request_body(base) is not None
    assert not parse_request_body(base).require_attended
    assert parse_request_body(base + "executor_bind: attended").require_attended


def test_directive_effective_require_attended_or():
    from services.git_integration_worker.cursor_auto.directive import (
        attendance_surface,
        effective_require_attended,
        parse_request_body,
    )
    from services.git_integration_worker.cursor_auto.queue import AutoJob

    body = parse_request_body("TYPE: DIRECTIVE\nrequire_attended: true")
    job_wire = AutoJob(
        job_id="j",
        thread_id="1",
        turn_number=1,
        subject="s",
        body="TYPE: DIRECTIVE",
        from_agent="web-anthropic",
        to_agent="cursor",
        desired_model="auto",
        desired_effort="medium",
        contract="implement",
        require_attended=True,
    )
    job_body = AutoJob(
        job_id="j2",
        thread_id="1",
        turn_number=1,
        subject="s",
        body="TYPE: DIRECTIVE\nrequire_attended: true",
        from_agent="web-anthropic",
        to_agent="cursor",
        desired_model="auto",
        desired_effort="medium",
        contract="implement",
    )
    assert effective_require_attended(job_wire, None)
    assert effective_require_attended(job_body, body)
    assert attendance_surface(job_wire, body) == "both"
    assert attendance_surface(job_body, body) == "body"


def test_enqueue_body_accepts_and_logs_extra_fields():
    from services.git_integration_worker.cursor_auto.wire_skew_events import (
        get_wire_skew_aggregate,
        reset_wire_skew_state_for_tests,
    )
    from services.git_integration_worker.routes.cursor_auto import EnqueueBody

    reset_wire_skew_state_for_tests()
    body = EnqueueBody(
        thread_id="1",
        turn_number=1,
        subject="s",
        body="",
        from_agent="web-anthropic",
        unknown_field=True,
        another_extra="x",
    )
    assert body.thread_id == "1"
    aggregate = get_wire_skew_aggregate()
    assert aggregate.get("mcp→giw/enqueue", 0) >= 2


def test_enqueue_body_binds_require_attended():
    from services.git_integration_worker.routes.cursor_auto import EnqueueBody

    body = EnqueueBody(
        thread_id="1",
        turn_number=1,
        subject="s",
        body="TYPE: DIRECTIVE",
        from_agent="web-anthropic",
        require_attended=True,
    )
    assert body.require_attended is True
