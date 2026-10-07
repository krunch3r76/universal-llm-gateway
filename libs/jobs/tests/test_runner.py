"""Idle kill has a grace window. Continued output has no wall-clock deadline."""

from __future__ import annotations

import time

import pytest

from jobs.tests.conftest import create, poll_until


@pytest.mark.offline
def test_idle_kill(client) -> None:
    created = create(client, "silent")
    started = time.monotonic()
    body = poll_until(
        client,
        "silent",
        created["run_id"],
        lambda row: row["status"] == "failed",
        20,
    )
    assert body["error"]["code"] == "idle_timeout"
    assert time.monotonic() - started < 2 + 10 + 3


@pytest.mark.offline
def test_no_wallclock_deadline(client) -> None:
    created = create(client, "chatter")
    body = poll_until(
        client,
        "chatter",
        created["run_id"],
        lambda row: row["status"] == "completed",
        15,
    )
    assert body["status"] == "completed"
    assert body["exit_code"] == 0
