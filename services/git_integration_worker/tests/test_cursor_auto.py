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
    admit_effort_override_rule_line,
    admit_model_override_rule_line,
    admit_model_pin_flags,
    assess_effort_pin,
    assess_escalation_pin,
    assess_model_pin,
    resolve_contract_disposition,
    resolve_desired_effort,
    resolve_desired_model,
    resolve_escalation,
    resolve_handoff_contract,
)


def test_wire_map_auto_by_contract():
    assert (
        resolve_desired_model("auto", contract="answer")["resolved_model_id"]
        == "cursor/grok-4.6"
    )
    assert (
        resolve_desired_model("auto", contract="investigate")["resolved_model_id"]
        == "cursor/grok-4.6"
    )
    assert (
        resolve_desired_model("auto", contract="recon")["resolved_model_id"]
        == "cursor/composer-2.5"
    )
    assert (
        resolve_desired_model("auto", contract="ask")["resolved_model_id"]
        == "cursor/composer-2.5"
    )
    assert (
        resolve_desired_model("auto", contract="seed")["resolved_model_id"]
        == "cursor/grok-4.6"
    )
    assert (
        resolve_desired_model("auto", contract="implement")["resolved_model_id"]
        == "cursor/composer-2.5"
    )
    assert (
        resolve_desired_model("auto", contract="light-bounded")["resolved_model_id"]
        == "cursor/grok-4.6"
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
        resolve_desired_model("cursor/grok-4.6")["resolved_model_id"]
        == "cursor/grok-4.6"
    )
    assert resolve_desired_model("cursor/grok-4.6")["honored"] is True
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
    assert "escalation=" in out["notes"]
    assert "team_dispatch(model=cdp/" in out["notes"]


def test_assess_model_pin_fable_refusal_names_escalation():
    """AC-S1-f: unbindable fable-5 refusal names CDP escalation route."""
    model, block = assess_model_pin(
        "fable-5",
        contract="investigate",
        body="TYPE: DIRECTIVE\n## Scope\nfoo\n",
    )
    assert model.get("rejected") is True
    assert block is not None
    assert "escalation=" in block
    assert "team_dispatch(model=cdp/" in block
    assert "cdp/opus-5" in block


def test_assess_model_pin_blocks_body_desired_model():
    model, block = assess_model_pin(
        "grok-4.6",
        contract="investigate",
        body="TYPE: DIRECTIVE\ndesired_model: grok-4.6\n",
    )
    assert block is not None
    assert "wire-only" in block
    assert model["resolved_model_id"] == "cursor/grok-4.6"


def test_assess_effort_pin_blocks_body_effort_line():
    effort, block = assess_effort_pin(
        "medium",
        body="TYPE: DIRECTIVE\neffort: high\n",
    )
    assert block is not None
    assert "wire-only" in block
    assert "high" in block
    assert effort["resolved_effort"] == "medium"


def test_assess_effort_pin_blocks_body_model_knobs_effort():
    effort, block = assess_effort_pin(
        "medium",
        body='TYPE: DIRECTIVE\nmodel_knobs={"effort": "high"}\n',
    )
    assert block is not None
    assert "wire-only" in block


def test_assess_effort_pin_honors_wire_only():
    effort, block = assess_effort_pin(
        "high",
        body="TYPE: DIRECTIVE\ndensity: dense\n## Scope\nfoo\n",
    )
    assert block is None
    assert effort["resolved_effort"] == "high"


def test_assess_effort_pin_allows_prose_mention_of_model_knobs():
    """AC3: describing the bad pattern in prose while wire pins correctly must admit."""
    prose = (
        "TYPE: DIRECTIVE\ndensity: dense\n## Scope\nfix knob relay\n"
        "intent: the chip wrongly said model_knobs={\"effort\": \"high\"} in the body\n"
        "vision: true guidance pins on wire\n"
    )
    effort, block = assess_effort_pin("high", body=prose)
    assert block is None
    assert effort["resolved_effort"] == "high"

def test_process_job_admits_prose_mention_with_wire_effort(monkeypatch):
    """AC3 live admit: prose quotes model_knobs effort; wire desired_effort=high admits."""
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
        job_id="j-prose-mention",
        thread_id="6655",
        turn_number=1,
        subject="prose mention",
        body=(
            "TYPE: DIRECTIVE\ndensity: dense\n## Scope\nfix guidance\n"
            "vision: test\n"
            "intent: defect was model_knobs={\"effort\": \"high\"} in body per chip\n"
        ),
        from_agent="web-anthropic",
        to_agent="cursor",
        desired_model="auto",
        desired_effort="high",
        contract="investigate",
    )

    asyncio.run(process_job(job, bus=bus))
    admit_call = bus.reply.await_args_list[0]
    assert admit_call.kwargs["subject"].startswith("status:admitted")
    admit_body = admit_call.kwargs["body"]
    assert "requested_effort=high" in admit_body or "resolved=high" in admit_body


def test_process_job_admits_wire_effort_first_attempt(monkeypatch):
    """AC4 live admit: corrected guidance — wire desired_effort only, no body pin."""
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
        job_id="j-wire-effort",
        thread_id="6655",
        turn_number=2,
        subject="wire effort",
        body=(
            "TYPE: DIRECTIVE\ndensity: dense\n## Scope\nimplement feature\n"
            "vision: pin effort on wire per Knob relay\n"
            "desired_model and desired_effort belong on agent_bus.request wire only\n"
        ),
        from_agent="web-anthropic",
        to_agent="cursor",
        desired_model="cursor/grok-4.6",
        desired_effort="high",
        contract="implement",
    )

    asyncio.run(process_job(job, bus=bus))
    admit_call = bus.reply.await_args_list[0]
    assert admit_call.kwargs["subject"].startswith("status:admitted")
    admit_body = admit_call.kwargs["body"]
    assert "model_honored=True" in admit_body
    assert "high" in admit_body


def test_admit_effort_override_rule_line_alias_normalization():
    effort = resolve_desired_effort("extra")
    line = admit_effort_override_rule_line(effort)
    assert line is not None
    assert line.startswith("effort_override_rule:")
    assert effort["notes"] in line


def test_admit_effort_override_rule_line_unchanged_when_requested_equals_resolved():
    effort = resolve_desired_effort("high")
    assert effort["requested"] == effort["resolved_effort"]
    assert admit_effort_override_rule_line(effort) is None


def test_process_job_blocks_body_effort_pin(monkeypatch):
    import asyncio
    import json
    from unittest.mock import AsyncMock, MagicMock

    from services.git_integration_worker.cursor_auto.handler import process_job
    from services.git_integration_worker.cursor_auto.queue import AutoJob

    bus = AsyncMock()
    bus.reply = AsyncMock(return_value=MagicMock(status_code=200, body={}))

    job = AutoJob(
        job_id="j-bad-effort",
        thread_id="6655",
        turn_number=1,
        subject="bad effort",
        body="TYPE: DIRECTIVE\ndensity: dense\n## Scope\nfoo\nvision: test\neffort: high\n",
        from_agent="web-anthropic",
        to_agent="cursor",
        desired_model="auto",
        desired_effort="medium",
        contract="investigate",
    )

    result = asyncio.run(process_job(job, bus=bus))
    assert result["terminal_status"] == "status:blocked"
    payload = json.loads(bus.reply.await_args.kwargs["body"])
    assert payload["reason"] == "effort_pin_refused"
    assert "wire-only" in payload["summary"]


def test_process_job_admit_surfaces_effort_override_rule(monkeypatch):
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
        job_id="j-admit-effort-rule",
        thread_id="6654",
        turn_number=1,
        subject="admit effort rule",
        body="TYPE: DIRECTIVE\ndensity: dense\n## Scope\nfoo\nvision: test\n",
        from_agent="web-anthropic",
        to_agent="cursor",
        desired_model="auto",
        desired_effort="extra",
        contract="investigate",
    )

    asyncio.run(process_job(job, bus=bus))
    admit_body = bus.reply.await_args_list[0].kwargs["body"]
    effort = resolve_desired_effort("extra")
    assert "effort_override_rule:" in admit_body
    assert effort["notes"] in admit_body


def test_admit_model_override_rule_line_auto_ladder():
    model = resolve_desired_model("auto", contract="investigate")
    line = admit_model_override_rule_line(model)
    assert line is not None
    assert line.startswith("model_override_rule:")
    assert model["notes"] in line
    assert "auto chose cursor/grok-4.6 for contract=investigate" in line


def test_admit_model_override_rule_line_honored_explicit_bare_pin():
    model = resolve_desired_model("grok-4.6")
    line = admit_model_override_rule_line(model)
    assert line is not None
    assert model["notes"] in line
    assert "honored explicit desired_model" in line


def test_admit_model_override_rule_line_unchanged_when_requested_equals_resolved():
    model = resolve_desired_model("cursor/grok-4.6")
    assert model["requested"] == model["resolved_model_id"]
    assert admit_model_override_rule_line(model) is None


def test_process_job_admit_surfaces_model_override_rule_auto(monkeypatch):
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
        job_id="j-admit-override-rule",
        thread_id="6654",
        turn_number=1,
        subject="admit override rule",
        body="TYPE: DIRECTIVE\ndensity: dense\n## Scope\nfoo\nvision: test\n",
        from_agent="web-anthropic",
        to_agent="cursor",
        desired_model="auto",
        desired_effort="medium",
        contract="investigate",
    )

    asyncio.run(process_job(job, bus=bus))
    admit_body = bus.reply.await_args_list[0].kwargs["body"]
    model = resolve_desired_model("auto", contract="investigate")
    assert "model_override_rule:" in admit_body
    assert model["notes"] in admit_body


def test_process_job_admit_omits_override_rule_when_requested_matches_resolved(
    monkeypatch,
):
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
        job_id="j-admit-no-override-rule",
        thread_id="6654",
        turn_number=1,
        subject="admit no override rule",
        body="TYPE: DIRECTIVE\ndensity: dense\n## Scope\nfoo\nvision: test\n",
        from_agent="web-anthropic",
        to_agent="cursor",
        desired_model="cursor/grok-4.6",
        desired_effort="medium",
        contract="investigate",
    )

    asyncio.run(process_job(job, bus=bus))
    admit_body = bus.reply.await_args_list[0].kwargs["body"]
    assert "model_override_rule:" not in admit_body


def test_admit_model_pin_flags_surfaces_effort_clamp():
    model = resolve_desired_model("grok-4.6")
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
        desired_model="cursor/grok-4.6",
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
