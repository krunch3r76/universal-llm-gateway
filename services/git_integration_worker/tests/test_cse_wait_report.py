"""Unit tests for mid-wait CSE paste (``cse_wait_report``)."""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import httpx

from services.git_integration_worker.cursor_auto.cse_wait_report import (
    build_wait_report_prompt_text,
    deliver_cse_wait_report,
    maybe_deliver_cse_wait_report,
)
from services.git_integration_worker.cursor_auto.queue import AutoJob


def test_build_wait_report_prompt_text_asks_park():
    text = build_wait_report_prompt_text(
        waiting_on="cursor-auto queue",
        queue_position=6,
        occupant="auto-be3fcb0060b7",
        job_id="job-1",
        thread_id="9538",
    )
    assert text.startswith("TYPE: WAITING")
    assert "waiting_on: cursor-auto queue" in text
    assert "queue_position: 6" in text
    assert "occupant: auto-be3fcb0060b7" in text
    assert "action: TYPE: PARKED" in text
    assert "waiting_on" in text
    assert "Do not stream" in text
    assert "status:done" not in text


def test_deliver_cse_wait_report_keeps_lane(monkeypatch):
    monkeypatch.setenv("PROJECT_ASK_URL", "http://127.0.0.1:9191")
    calls: list[dict] = []

    def _post(method: str, url: str, *, json=None, timeout: float):
        calls.append({"method": method, "url": url, "json": json, "timeout": timeout})
        resp = MagicMock(spec=httpx.Response)
        resp.status_code = 200
        resp.content = b'{"ok": true, "send_verified": true}'
        resp.json.return_value = {"ok": True, "send_verified": True}
        resp.text = ""
        return resp

    result = deliver_cse_wait_report(
        chat_url="https://claude.ai/cowork/cse_abc",
        registration_id="reg-1",
        prompt_text="TYPE: WAITING\nwaiting_on: queue",
        post=_post,
    )
    assert result["ok"] is True
    body = calls[0]["json"]
    assert body["chat_url"] == "https://claude.ai/cowork/cse_abc"
    assert body["registration_id"] == "reg-1"
    assert body.get("reattach") is None
    assert body["retain_lane"] is True
    assert calls[0]["timeout"] == 120.0


def test_maybe_deliver_wait_report_skips_ide_class():
    job = AutoJob(
        job_id="j1",
        thread_id="1",
        turn_number=1,
        subject="s",
        body="b",
        from_agent="cursor",
        to_agent="cursor",
        desired_model="auto",
        desired_effort="medium",
        contract="implement",
        cse_chat_url="https://claude.ai/cowork/cse_x",
    )
    result = asyncio.run(
        maybe_deliver_cse_wait_report(job, waiting_on="cursor-auto queue")
    )
    assert result["skipped"] is True
    assert result["reason"] == "not_chat_delivery_capable"


def _job(**extra) -> AutoJob:
    fields = dict(
        job_id="j-wait",
        thread_id="9538",
        turn_number=1,
        subject="s",
        body="b",
        from_agent="web-anthropic",
        to_agent="cursor-auto",
        desired_model="auto",
        desired_effort="medium",
        contract="implement",
        cse_chat_url="https://claude.ai/cowork/cse_x",
    )
    fields.update(extra)
    return AutoJob(**fields)


def test_should_emit_wait_report_when_occupied():
    from services.git_integration_worker.cursor_auto.cse_wait_report import (
        should_emit_wait_report,
    )

    job = _job()
    assert should_emit_wait_report(job, occupied=True, queue_position=1) is True
    assert should_emit_wait_report(job, occupied=False, queue_position=1) is False
    assert should_emit_wait_report(job, occupied=False, queue_position=6) is True


def test_should_emit_wait_report_skips_hops_and_ide():
    from services.git_integration_worker.cursor_auto.cse_wait_report import (
        should_emit_wait_report,
    )

    hop = _job(continuity_hop=True)
    assert should_emit_wait_report(hop, occupied=True, queue_position=2) is False
    ide = _job(from_agent="cursor")
    assert should_emit_wait_report(ide, occupied=True, queue_position=2) is False
    no_cse = _job(cse_chat_url=None, cse_registration_id=None)
    assert should_emit_wait_report(no_cse, occupied=True, queue_position=2) is True


def test_should_emit_wait_report_cdp_operator_without_stamp():
    from services.git_integration_worker.cursor_auto.cse_wait_report import (
        should_emit_wait_report,
    )

    job = _job(
        from_agent="cdp-operator-6655-day5i",
        cse_chat_url=None,
        cse_registration_id=None,
    )
    assert should_emit_wait_report(job, occupied=True, queue_position=2) is True


def test_serial_queue_occupant_skips_hops():
    from services.git_integration_worker.cursor_auto import queue as queue_mod
    from services.git_integration_worker.cursor_auto.cse_wait_report import (
        serial_queue_occupant,
    )

    q = queue_mod.reset_queue_for_tests(durable=False)
    hop = q.enqueue(
        thread_id="9538",
        turn_number=1,
        subject="hop",
        body="TYPE: CONTINUITY_HANDOFF\n",
        from_agent="web-anthropic",
        to_agent="cursor-auto",
        desired_model="auto",
        desired_effort="high",
        contract="light-bounded",
        continuity_hop=True,
    )
    q.claim_job(hop.job_id)
    assert serial_queue_occupant(q) is None
    serial = q.enqueue(
        thread_id="9539",
        turn_number=1,
        subject="work",
        body="TYPE: DIRECTIVE\n",
        from_agent="web-anthropic",
        to_agent="cursor-auto",
        desired_model="auto",
        desired_effort="medium",
        contract="implement",
    )
    q.claim_job(serial.job_id)
    occ = serial_queue_occupant(q)
    assert occ is not None
    assert occ.job_id == serial.job_id
