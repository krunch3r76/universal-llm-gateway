"""Row 21 Slice 1/2 — continuity hop ≠ backtrack; successor mailbox normalize."""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.git_integration_worker.cursor_auto.directive import (
    is_continuity_hop_request,
    parse_request_body,
)
from services.git_integration_worker.cursor_auto.handler_terminal import (
    post_terminal_status,
)
from services.git_integration_worker.cursor_auto.queue import AutoJobQueue
from services.git_integration_worker.cursor_auto.supersede import (
    post_superseded_terminal,
    supersede_same_thread_inflight,
)
from services.git_integration_worker.cursor_sdk_supersede import (
    register_live_run,
    unregister_live_run,
)
from services.git_integration_worker.routes import cursor_auto as routes_mod
from services.git_integration_worker.routes.cursor_auto import EnqueueBody, enqueue


def test_continuity_hop_first_line_type():
    body = "TYPE: CONTINUITY_HANDOFF\ncontract: implement\nscope: CDP launch only\n"
    is_hop, token = is_continuity_hop_request(body)
    assert is_hop is True
    assert token == "TYPE:CONTINUITY_HANDOFF"
    parsed = parse_request_body(body)
    assert parsed is not None
    assert parsed.turn_type == "CONTINUITY_HANDOFF"


def test_continuity_hop_rejects_prose_only_directive():
    body = (
        "TYPE: DIRECTIVE\nintent: continuity hop to successor CSE\n"
        "scope: CDP launch only\n"
    )
    is_hop, token = is_continuity_hop_request(body)
    assert is_hop is False
    assert token == "none"


def test_continuity_hop_wire_flag_alone():
    is_hop, token = is_continuity_hop_request("TYPE: DIRECTIVE\n", wire_flag=True)
    assert is_hop is True
    assert token == "wire:continuity_hop"


def test_continuity_hop_type_not_first_line_is_not_hop():
    body = "arc: foo\nTYPE: CONTINUITY_HANDOFF\nscope: CDP\n"
    is_hop, token = is_continuity_hop_request(body)
    assert is_hop is False
    assert token == "none"


@pytest.fixture
def live_run():
    run = MagicMock()
    register_live_run(
        dispatch_id="auto-hop-live",
        thread_id="T-hop",
        source_repo="/repo",
        run=run,
    )
    yield run
    unregister_live_run(dispatch_id="auto-hop-live")


@pytest.mark.asyncio
async def test_hop_enqueue_leaves_claimed_job_running(live_run, monkeypatch):
    """#1584→hop pattern: claimed job on T survives hop enqueue."""
    from services.git_integration_worker.cursor_auto import queue as queue_mod

    q = queue_mod.reset_queue_for_tests(durable=False)
    old = q.enqueue(
        thread_id="T-hop",
        turn_number=1584,
        subject="commission in flight",
        body="TYPE: DIRECTIVE\ncontract: implement\n## Scope\nlibs/foo\n",
        from_agent="cdp",
        to_agent="cursor",
        desired_model="auto",
        desired_effort="medium",
        contract="implement",
    )
    assert q.claim_next().job_id == old.job_id

    hop_tasks: list = []

    async def _capture_hop(job, *, queue, incumbent):
        hop_tasks.append((job.job_id, incumbent.job_id))
        return {"ok": True}

    monkeypatch.setattr(routes_mod, "get_queue", lambda: q)
    monkeypatch.setattr(routes_mod, "get_registry", lambda: MagicMock(is_live=lambda: True))
    monkeypatch.setattr(routes_mod, "run_continuity_hop_concurrent", _capture_hop)

    body = EnqueueBody(
        thread_id="T-hop",
        turn_number=1585,
        subject="continuity hop",
        body="TYPE: CONTINUITY_HANDOFF\ncontract: implement\nscope: CDP launch only\n",
        from_agent="cdp",
        to_agent="cursor",
        contract="implement",
    )
    resp = await enqueue(body)
    assert resp.status_code == 200
    content = resp.body
    if hasattr(content, "decode"):
        payload = json.loads(content.decode())
    else:
        payload = json.loads(content)

    assert payload["continuity_hop"] is True
    assert payload["matched_token"] == "TYPE:CONTINUITY_HANDOFF"
    assert payload["superseded"] is None
    assert q.get(old.job_id).status == "claimed"
    assert not q.is_superseded(old.job_id)
    # Let the create_task run.
    await asyncio.sleep(0)
    assert hop_tasks and hop_tasks[0][1] == old.job_id
    live_run.cancel.assert_not_called()


@pytest.mark.asyncio
async def test_non_hop_same_thread_still_supersedes(live_run):
    from services.git_integration_worker.cursor_auto import queue as queue_mod

    q = queue_mod.reset_queue_for_tests(durable=False)
    old = q.enqueue(
        thread_id="T-hop",
        turn_number=1,
        subject="old",
        body="TYPE: DIRECTIVE\n## Scope\nx\n",
        from_agent="web-anthropic",
        to_agent="cursor",
        desired_model="auto",
        desired_effort="medium",
        contract="implement",
    )
    q.claim_next()
    new = q.enqueue(
        thread_id="T-hop",
        turn_number=2,
        subject="backtrack re-issue",
        body="TYPE: DIRECTIVE\n## Scope\ny\n",
        from_agent="web-anthropic",
        to_agent="cursor",
        desired_model="auto",
        desired_effort="medium",
        contract="implement",
    )
    evidence = await supersede_same_thread_inflight(new, queue=q)
    assert evidence is not None
    assert q.is_superseded(old.job_id)


@pytest.mark.asyncio
async def test_terminal_normalizes_cdp_to_web_anthropic():
    """Slice 2 / #1588 baseline — to=cdp must become web-anthropic."""
    from services.git_integration_worker.cursor_auto import queue as queue_mod

    q = queue_mod.reset_queue_for_tests(durable=False)
    job = q.enqueue(
        thread_id="T-addr",
        turn_number=1,
        subject="re-issue me",
        body="TYPE: DIRECTIVE\n",
        from_agent="cdp",
        to_agent="cursor",
        desired_model="auto",
        desired_effort="medium",
        contract="implement",
    )
    q.claim_next()
    replies: list[dict] = []

    async def _reply(**kwargs):
        replies.append(kwargs)
        return MagicMock(status_code=200)

    client = MagicMock()
    client.reply = _reply
    await post_terminal_status(
        job,
        client=client,
        queue=q,
        summary="done",
        disposition="ok",
        payload={"summary": "done"},
        contract="implement",
        terminal_status="status:done",
    )
    assert replies[0]["to_agent"] == "web-anthropic"


@pytest.mark.asyncio
async def test_superseded_terminal_names_dispatch_and_reissue_subject():
    from services.git_integration_worker.cursor_auto import queue as queue_mod

    q = queue_mod.reset_queue_for_tests(durable=False)
    old = q.enqueue(
        thread_id="T-sup",
        turn_number=1,
        subject="voided episode",
        body="TYPE: DIRECTIVE\n",
        from_agent="cdp",
        to_agent="cursor",
        desired_model="auto",
        desired_effort="medium",
        contract="implement",
    )
    q.claim_next()
    new = q.enqueue(
        thread_id="T-sup",
        turn_number=2,
        subject="operator backtrack re-issue",
        body="TYPE: DIRECTIVE\n## Scope\nz\n",
        from_agent="cdp",
        to_agent="cursor",
        desired_model="auto",
        desired_effort="medium",
        contract="implement",
    )
    q.mark_superseded(old.job_id, superseded_by=new.job_id)
    old.superseded_by = new.job_id

    replies: list[dict] = []

    async def _reply(**kwargs):
        replies.append(kwargs)
        return MagicMock(status_code=200)

    client = MagicMock()
    client.reply = _reply
    result = await post_superseded_terminal(
        old,
        client=client,
        queue=q,
        dispatch_id="auto-killed-1",
    )
    assert replies[0]["to_agent"] == "web-anthropic"
    body = json.loads(replies[0]["body"])
    assert body["dispatch_id"] == "auto-killed-1"
    assert body["re_issue_subject"] == "operator backtrack re-issue"
    assert result["dispatch_id"] == "auto-killed-1"
