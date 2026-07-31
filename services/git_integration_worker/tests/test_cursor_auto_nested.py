
def test_directive_parse_requires_type_line():
    from services.git_integration_worker.cursor_auto.directive import parse_request_body

    assert parse_request_body("hello") is None
    body = "TYPE: DIRECTIVE\ndensity: dense\n## Scope\nlibs/foo\nvision: mechanical — parse fixture\n"
    parsed = parse_request_body(body)
    assert parsed is not None
    assert parsed.density == "dense"
    assert parsed.require_attended is False


def test_process_job_require_attended_wire_short_circuits(monkeypatch):
    import asyncio
    import json
    from unittest.mock import AsyncMock, MagicMock

    from services.git_integration_worker.cursor_auto.handler import process_job
    from services.git_integration_worker.cursor_auto.queue import AutoJob

    bus = AsyncMock()
    bus.reply = AsyncMock(return_value=MagicMock(status_code=200, body={}))
    submit = AsyncMock()

    monkeypatch.setattr(
        "services.git_integration_worker.cursor_auto.handler.submit_nested_dispatch",
        submit,
    )
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_auto.gate_serialize.sdk_dispatch_gate_stats",
        lambda: {"active": 0, "queued": 0, "limit": 1},
    )

    job = AutoJob(
        job_id="j-att-wire",
        thread_id="5867",
        turn_number=8,
        subject="DIRECTIVE attended",
        body="TYPE: DIRECTIVE\ndensity: dense\n## Scope\nlibs/foo\nvision: mechanical — test fixture\n",
        from_agent="web-anthropic",
        to_agent="cursor",
        desired_model="composer-2.5",
        desired_effort="medium",
        contract="implement",
        require_attended=True,
    )

    result = asyncio.run(process_job(job, bus=bus))
    assert result["terminal_status"] == "status:needs-attended"
    assert result["disposition"] == "needs-attended"
    submit.assert_not_awaited()
    terminal_call = bus.reply.await_args_list[-1]
    payload = json.loads(terminal_call.kwargs["body"])
    assert payload["reason"] == "operator_require_attended"
    assert bus.reply.await_count == 2


def test_process_job_require_attended_body_short_circuits(monkeypatch):
    import asyncio
    import json
    from unittest.mock import AsyncMock, MagicMock

    from services.git_integration_worker.cursor_auto.handler import process_job
    from services.git_integration_worker.cursor_auto.queue import AutoJob

    bus = AsyncMock()
    bus.reply = AsyncMock(return_value=MagicMock(status_code=200, body={}))
    submit = AsyncMock()

    monkeypatch.setattr(
        "services.git_integration_worker.cursor_auto.handler.submit_nested_dispatch",
        submit,
    )
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_auto.gate_serialize.sdk_dispatch_gate_stats",
        lambda: {"active": 0, "queued": 0, "limit": 1},
    )

    job = AutoJob(
        job_id="j-att-body",
        thread_id="5867",
        turn_number=8,
        subject="DIRECTIVE attended",
        body="TYPE: DIRECTIVE\nrequire_attended: true\ndensity: dense\n## Scope\nlibs/foo\nvision: mechanical — test fixture\n",
        from_agent="web-anthropic",
        to_agent="cursor",
        desired_model="composer-2.5",
        desired_effort="medium",
        contract="implement",
    )

    result = asyncio.run(process_job(job, bus=bus))
    assert result["terminal_status"] == "status:needs-attended"
    submit.assert_not_awaited()
    payload = json.loads(bus.reply.await_args_list[-1].kwargs["body"])
    assert payload["reason"] == "operator_require_attended"


def test_process_job_require_attended_precedence_at_capacity(monkeypatch):
    import asyncio
    import json
    from unittest.mock import AsyncMock, MagicMock

    from services.git_integration_worker.cursor_auto.handler import process_job
    from services.git_integration_worker.cursor_auto.queue import AutoJob

    bus = AsyncMock()
    bus.reply = AsyncMock(return_value=MagicMock(status_code=200, body={}))
    submit = AsyncMock()

    monkeypatch.setattr(
        "services.git_integration_worker.cursor_auto.handler.submit_nested_dispatch",
        submit,
    )
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_auto.gate_serialize.sdk_dispatch_gate_stats",
        lambda: {"active": 1, "queued": 0, "limit": 1},
    )

    job = AutoJob(
        job_id="j-att-cap",
        thread_id="5867",
        turn_number=8,
        subject="DIRECTIVE attended",
        body="TYPE: DIRECTIVE\ndensity: dense\n## Scope\nlibs/foo\nvision: mechanical — test fixture\n",
        from_agent="web-anthropic",
        to_agent="cursor",
        desired_model="composer-2.5",
        desired_effort="medium",
        contract="implement",
        require_attended=True,
    )

    asyncio.run(process_job(job, bus=bus))
    payload = json.loads(bus.reply.await_args_list[-1].kwargs["body"])
    assert payload["reason"] == "operator_require_attended"
    submit.assert_not_awaited()


def test_process_job_gate_fallback_without_require_attended(monkeypatch):
    import asyncio
    import json
    from unittest.mock import AsyncMock, MagicMock

    from services.git_integration_worker.cursor_auto.handler import process_job
    from services.git_integration_worker.cursor_auto.queue import AutoJob

    bus = AsyncMock()
    bus.reply = AsyncMock(return_value=MagicMock(status_code=200, body={}))

    monkeypatch.setattr(
        "services.git_integration_worker.cursor_auto.gate_serialize.sdk_dispatch_gate_stats",
        lambda: {"active": 1, "queued": 0, "limit": 1},
    )
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_auto.handler.CursorDispatchLedger.instance",
        lambda: MagicMock(lease_snapshot=MagicMock(return_value={})),
    )
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_auto.admit_gates.fetch_thread_turns",
        AsyncMock(return_value=[]),
    )

    job = AutoJob(
        job_id="j-gate-fallback",
        thread_id="5867",
        turn_number=8,
        subject="DIRECTIVE",
        body="TYPE: DIRECTIVE\ndensity: dense\n## Scope\nlibs/foo\nvision: mechanical — test fixture\n",
        from_agent="web-anthropic",
        to_agent="cursor",
        desired_model="composer-2.5",
        desired_effort="medium",
        contract="implement",
    )

    asyncio.run(process_job(job, bus=bus))
    payload = json.loads(bus.reply.await_args_list[-1].kwargs["body"])
    assert payload["reason"] == "nest_park_without_holder"


def test_process_job_require_attended_gate_counters_unchanged(monkeypatch):
    import asyncio
    from unittest.mock import AsyncMock, MagicMock

    from services.git_integration_worker.cursor_auto import gate_serialize
    from services.git_integration_worker.cursor_auto.handler import process_job
    from services.git_integration_worker.cursor_auto.queue import AutoJob

    stats = {"active": 0, "queued": 0, "limit": 1}

    def _stats():
        return dict(stats)

    monkeypatch.setattr(gate_serialize, "sdk_dispatch_gate_stats", _stats)

    bus = AsyncMock()
    bus.reply = AsyncMock(return_value=MagicMock(status_code=200, body={}))
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_auto.handler.submit_nested_dispatch",
        AsyncMock(),
    )

    before = gate_serialize.sdk_dispatch_gate_stats()
    job = AutoJob(
        job_id="j-gate-pure",
        thread_id="5867",
        turn_number=8,
        subject="DIRECTIVE attended",
        body="TYPE: DIRECTIVE\ndensity: dense\n## Scope\nlibs/foo\nvision: mechanical — test fixture\n",
        from_agent="web-anthropic",
        to_agent="cursor",
        desired_model="composer-2.5",
        desired_effort="medium",
        contract="implement",
        require_attended=True,
    )

    asyncio.run(process_job(job, bus=bus))
    after = gate_serialize.sdk_dispatch_gate_stats()
    assert before == after


def test_process_job_nested_implement_dispatches(monkeypatch):
    import asyncio
    from unittest.mock import AsyncMock, MagicMock

    from services.git_integration_worker.cursor_auto.handler import process_job
    from services.git_integration_worker.cursor_auto.queue import AutoJob

    bus = AsyncMock()
    bus.reply = AsyncMock(return_value=MagicMock(status_code=200, body={}))

    submit = AsyncMock(
        return_value={
            "ok": True,
            "dispatch_id": "auto-abc123",
            "execution_id": "exec-auto-abc123",
        }
    )
    polled = AsyncMock(
        return_value={
            "ok": True,
            "terminal": True,
            "status": "completed",
        }
    )
    sdk_body = AsyncMock(return_value='{"status":"complete"}')
    relay = AsyncMock(return_value={"ok": True, "status_code": 200})
    wake = AsyncMock(return_value={"ok": True, "status_code": 200})

    monkeypatch.setattr(
        "services.git_integration_worker.cursor_auto.handler.submit_nested_dispatch",
        submit,
    )
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_auto.handler.poll_dispatch_terminal",
        polled,
    )
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_auto.handler.fetch_sdk_closeout_body",
        sdk_body,
    )
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_auto.nested_outcome.post_operator_closeout",
        relay,
    )
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_auto.nested_outcome.post_operator_wake",
        wake,
    )
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_auto.handler.CursorDispatchLedger.instance",
        lambda: MagicMock(lease_snapshot=MagicMock(return_value={})),
    )

    job = AutoJob(
        job_id="j1",
        thread_id="5867",
        turn_number=8,
        subject="DIRECTIVE G2.1",
        body="TYPE: DIRECTIVE\ndensity: dense\n## Scope\nlibs/session_store/\nvision: mechanical — nested dispatch fixture\n",
        from_agent="web-anthropic",
        to_agent="cursor",
        desired_model="composer-2.5",
        desired_effort="medium",
        contract="implement",
    )

    result = asyncio.run(process_job(job, bus=bus))
    assert result["ok"] is True
    assert result["phase"] == "nested_dispatch"
    submit.assert_awaited_once()
    assert submit.await_args.kwargs["handoff_contract"] == "pure-mechanical"
    relay.assert_awaited_once()
    wake.assert_awaited_once()
    wake_kwargs = wake.await_args.kwargs
    assert wake_kwargs["dispatch_id"] == "auto-abc123"
    assert wake_kwargs["request_turn"] == "8"


def test_process_job_nested_skips_wake_when_closeout_fails(monkeypatch):
    import asyncio
    from unittest.mock import AsyncMock, MagicMock

    from services.git_integration_worker.cursor_auto.handler import process_job
    from services.git_integration_worker.cursor_auto.queue import AutoJob

    bus = AsyncMock()
    bus.reply = AsyncMock(return_value=MagicMock(status_code=200, body={}))

    submit = AsyncMock(
        return_value={
            "ok": True,
            "dispatch_id": "auto-abc123",
            "execution_id": "exec-auto-abc123",
        }
    )
    polled = AsyncMock(
        return_value={
            "ok": True,
            "terminal": True,
            "status": "completed",
        }
    )
    sdk_body = AsyncMock(return_value='{"status":"complete"}')
    relay = AsyncMock(return_value={"ok": False, "status_code": 500})
    wake = AsyncMock(return_value={"ok": True, "status_code": 200})

    monkeypatch.setattr(
        "services.git_integration_worker.cursor_auto.handler.submit_nested_dispatch",
        submit,
    )
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_auto.handler.poll_dispatch_terminal",
        polled,
    )
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_auto.handler.fetch_sdk_closeout_body",
        sdk_body,
    )
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_auto.nested_outcome.post_operator_closeout",
        relay,
    )
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_auto.nested_outcome.post_operator_wake",
        wake,
    )
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_auto.handler.CursorDispatchLedger.instance",
        lambda: MagicMock(lease_snapshot=MagicMock(return_value={})),
    )

    job = AutoJob(
        job_id="j2",
        thread_id="5867",
        turn_number=9,
        subject="DIRECTIVE G2.1",
        body="TYPE: DIRECTIVE\ndensity: dense\n## Scope\nlibs/session_store/\nvision: mechanical — nested dispatch fixture\n",
        from_agent="web-anthropic",
        to_agent="cursor",
        desired_model="composer-2.5",
        desired_effort="medium",
        contract="implement",
    )

    result = asyncio.run(process_job(job, bus=bus))
    assert result["ok"] is False
    assert result["wake"] == {
        "ok": False,
        "skipped": True,
        "reason": "closeout_not_ok",
    }
    wake.assert_not_awaited()


def test_post_operator_wake_token_guard_and_subject_truncation(monkeypatch):
    import asyncio
    from unittest.mock import AsyncMock, MagicMock

    from services.git_integration_worker.cursor_auto.nested_sdk import (
        _wake_subject,
        post_operator_wake,
    )
    from services.git_integration_worker.cursor_auto.queue import AutoJob

    bus = AsyncMock()
    bus.reply = AsyncMock(return_value=MagicMock(status_code=200, body={}))

    job = AutoJob(
        job_id="j3",
        thread_id="5867",
        turn_number=12,
        subject="DIRECTIVE",
        body="TYPE: DIRECTIVE",
        from_agent="web-anthropic",
        to_agent="cursor",
        desired_model="composer-2.5",
        desired_effort="medium",
        contract="implement",
    )

    result = asyncio.run(
        post_operator_wake(
            job,
            dispatch_id="auto-abc123",
            request_turn="12",
            closeout_status="status:done",
            bus=bus,
        )
    )
    assert result["ok"] is True
    bus.reply.assert_awaited_once()
    kwargs = bus.reply.await_args.kwargs
    assert kwargs["body"].startswith("TYPE: WAKE")
    assert "status:done" not in kwargs["body"]
    assert "status:failed" not in kwargs["body"]
    assert "status:needs-attended" not in kwargs["body"]
    assert "closeout_status: done" in kwargs["body"]
    assert kwargs["subject"] == "WAKE — closeout relayed · auto-abc123"

    long_id = "auto-" + ("x" * 100)
    subject = _wake_subject(long_id)
    assert len(subject) <= 80
    assert subject.endswith("…")
    assert "status:done" not in subject


def test_post_operator_wake_token_guard_blocks_forbidden_dispatch_id(monkeypatch):
    import asyncio
    from unittest.mock import AsyncMock, MagicMock

    from services.git_integration_worker.cursor_auto.nested_sdk import (
        post_operator_wake,
    )
    from services.git_integration_worker.cursor_auto.queue import AutoJob

    bus = AsyncMock()
    bus.reply = AsyncMock(return_value=MagicMock(status_code=200, body={}))

    job = AutoJob(
        job_id="j4",
        thread_id="5867",
        turn_number=12,
        subject="DIRECTIVE",
        body="TYPE: DIRECTIVE",
        from_agent="web-anthropic",
        to_agent="cursor",
        desired_model="composer-2.5",
        desired_effort="medium",
        contract="implement",
    )

    result = asyncio.run(
        post_operator_wake(
            job,
            dispatch_id="auto-status:done-trap",
            request_turn="12",
            closeout_status="complete",
            bus=bus,
        )
    )
    assert result == {"ok": False, "reason": "wake_token_guard"}
    bus.reply.assert_not_awaited()
