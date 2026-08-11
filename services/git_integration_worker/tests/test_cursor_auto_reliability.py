"""Reliability primitives — job deadline/TTL, progress turns, request_id echo."""

from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from services.git_integration_worker.cursor_auto.dispatch_progress import (
    ProgressEmitter,
    strip_completion_tokens,
)
from services.git_integration_worker.cursor_auto.handler_deadline import (
    deadline_terminal,
)
from services.git_integration_worker.cursor_auto.handler_terminal import (
    post_terminal_status,
)
from services.git_integration_worker.cursor_auto.job_deadline import (
    deadline_verdict,
    parse_deadline,
)
from services.git_integration_worker.cursor_auto.queue import AutoJob, AutoJobQueue


class _Reply:
    def __init__(self, status_code: int = 200) -> None:
        self.status_code = status_code
        self.body = ""


class _FakeBus:
    def __init__(self) -> None:
        self.posts: list[dict[str, Any]] = []

    async def reply(self, **kwargs: Any) -> _Reply:
        self.posts.append(kwargs)
        return _Reply()


class _FakeQueue:
    def __init__(self) -> None:
        self.done: list[tuple[str, bool]] = []

    def mark_done(
        self,
        job_id: str,
        *,
        failed: bool = False,
        terminal_reason: str | None = None,
    ) -> None:
        self.done.append((job_id, failed))


def _job(body: str, *, enqueued_at: float | None = None) -> AutoJob:
    return AutoJob(
        job_id="job-1",
        thread_id="6328",
        turn_number=4,
        subject="deadline probe",
        body=body,
        from_agent="web-anthropic",
        to_agent="cursor",
        desired_model="auto",
        desired_effort="medium",
        contract="implement",
        request_id="req-xyz",
        enqueued_at=enqueued_at if enqueued_at is not None else time.monotonic(),
    )


# --- deadline parsing ------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected_s"),
    [("+15m", 900.0), ("+2h", 7200.0), ("+90s", 90.0), ("30m", 1800.0)],
)
def test_relative_deadline_windows(raw: str, expected_s: float):
    deadline, bad = parse_deadline(f"TYPE: DIRECTIVE\ndeadline: {raw}\n")
    assert bad is None
    assert deadline.relative_s == expected_s


def test_absolute_deadline_parses_iso_with_z():
    deadline, bad = parse_deadline("deadline: 2026-07-29T18:00:00Z\n")
    assert bad is None
    assert deadline.absolute == datetime(2026, 7, 29, 18, 0, tzinfo=UTC)


def test_absolute_deadline_in_the_past_is_expired():
    past = (datetime.now(UTC) - timedelta(minutes=5)).isoformat()
    deadline, _ = parse_deadline(f"deadline: {past}\n")
    assert deadline.expired(elapsed_s=0.0) is True


def test_absent_deadline_yields_no_ttl():
    verdict = deadline_verdict("TYPE: DIRECTIVE\nscope: x\n", enqueued_at=0.0)
    assert verdict.state == "absent"
    assert verdict.blocking is False


def test_unparseable_deadline_is_blocking_not_ignored():
    verdict = deadline_verdict("deadline: soonish\n", enqueued_at=time.monotonic())
    assert verdict.state == "unparseable"
    assert verdict.blocking is True
    assert verdict.raw == "soonish"


def test_relative_deadline_expires_against_enqueue_age():
    stale = time.monotonic() - 600.0
    verdict = deadline_verdict("deadline: +5m\n", enqueued_at=stale)
    assert verdict.state == "expired"
    assert verdict.elapsed_s >= 600.0


def test_live_relative_deadline_does_not_block():
    verdict = deadline_verdict("deadline: +1h\n", enqueued_at=time.monotonic())
    assert verdict.state == "live"
    assert verdict.blocking is False


# --- deadline terminal ------------------------------------------------------


@pytest.mark.asyncio
async def test_expired_job_terminates_failed_before_execution():
    bus, queue = _FakeBus(), _FakeQueue()
    job = _job("deadline: +1s\n", enqueued_at=time.monotonic() - 60.0)
    result = await deadline_terminal(job, client=bus, queue=queue)
    assert result is not None
    assert result["terminal_status"] == "status:failed"
    assert result["disposition"] == "expired"
    assert "expired" in bus.posts[0]["body"]
    assert queue.done == [("job-1", True)]


@pytest.mark.asyncio
async def test_unparseable_deadline_terminates_blocked_with_fix_hint():
    bus, queue = _FakeBus(), _FakeQueue()
    result = await deadline_terminal(_job("deadline: whenever\n"), client=bus, queue=queue)
    assert result["terminal_status"] == "status:blocked"
    assert "deadline_unparseable" in bus.posts[0]["body"]
    assert "ISO-8601" in bus.posts[0]["body"]


@pytest.mark.asyncio
async def test_job_without_deadline_proceeds():
    bus, queue = _FakeBus(), _FakeQueue()
    assert await deadline_terminal(_job("scope: x\n"), client=bus, queue=queue) is None
    assert bus.posts == []


# --- progress turns ---------------------------------------------------------


def test_progress_body_cannot_carry_a_completion_token():
    cleaned = strip_completion_tokens('{"ledger": "status:done"}')
    assert "status:done" not in cleaned
    assert "state-done" in cleaned


@pytest.mark.asyncio
async def test_progress_emitter_respects_its_interval():
    bus = _FakeBus()
    emitter = ProgressEmitter(_job("scope: x\n"), client=bus, interval_s=10_000.0)
    await emitter.maybe_emit({"status": "running"})
    assert bus.posts == []


@pytest.mark.asyncio
async def test_progress_emitter_posts_once_interval_elapsed():
    bus = _FakeBus()
    emitter = ProgressEmitter(_job("scope: x\n"), client=bus, interval_s=0.0)
    await emitter.maybe_emit({"status": "running"})
    assert len(bus.posts) == 1
    assert "req-xyz" in bus.posts[0]["body"]
    assert "status:" not in bus.posts[0]["subject"]


# --- request_id echo --------------------------------------------------------


@pytest.mark.asyncio
async def test_terminal_payload_echoes_request_id():
    bus, queue = _FakeBus(), _FakeQueue()
    await post_terminal_status(
        _job("scope: x\n"),
        client=bus,
        queue=queue,
        summary="done",
        disposition="answered",
        contract="answer",
        payload={"summary": "done"},
    )
    assert "req-xyz" in bus.posts[0]["body"]


def test_queue_carries_request_id_through_enqueue():
    queue = AutoJobQueue()
    job = queue.enqueue(
        thread_id="6328",
        turn_number=1,
        subject="s",
        body="b",
        from_agent="web-anthropic",
        to_agent="cursor",
        desired_model="auto",
        desired_effort="medium",
        contract="answer",
        request_id="req-from-intake",
    )
    assert queue.get(job.job_id).request_id == "req-from-intake"
