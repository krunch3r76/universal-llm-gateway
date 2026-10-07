"""Status representation, wait bounds, and progress wakeups."""

from __future__ import annotations

import time

import pytest

from jobs.tests.conftest import HEADERS, create, poll_until


@pytest.mark.offline
def test_exit_code_in_representation(client) -> None:
    created = create(client, "exit3")
    body = poll_until(
        client,
        "exit3",
        created["run_id"],
        lambda row: row["status"] == "failed",
        10,
    )
    assert body["exit_code"] == 3
    assert body["error"]["code"] == "exit_nonzero"
    assert body["source"] == "jobs.journal"
    fetched = client.get(
        f"/api/v1/jobs/exit3/runs/{created['run_id']}",
        headers=HEADERS,
    )
    assert fetched.status_code == 200


@pytest.mark.offline
def test_wait_bounds(client) -> None:
    created = create(client, "exit3")
    run_id = created["run_id"]
    for value in (61, -1):
        response = client.get(
            f"/api/v1/jobs/exit3/runs/{run_id}",
            headers=HEADERS,
            params={"wait": value},
        )
        assert response.status_code == 422
        assert response.json()["code"] == "wait_out_of_range"


@pytest.mark.offline
def test_wait_returns_on_progress(client) -> None:
    created = create(client, "ticker")
    run_id = created["run_id"]
    poll_until(client, "ticker", run_id, lambda row: row["status"] == "running", 10)
    started = time.monotonic()
    response = client.get(
        f"/api/v1/jobs/ticker/runs/{run_id}",
        headers=HEADERS,
        params={"wait": 30},
    )
    elapsed = time.monotonic() - started
    assert response.status_code == 200
    assert elapsed < 30
    assert response.json()["progress_seq"] >= 1
