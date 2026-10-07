"""DELETE cancel. Admitted does not signal; running does; terminal is 409."""

from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient

from jobs.server import create_app
from jobs.spec import load_registry
from jobs.tests.conftest import HEADERS, TOKEN, create, make_spec, poll_until


@pytest.mark.offline
def test_delete_running_then_repeat(client) -> None:
    created = create(client, "ticker")
    run_id = created["run_id"]
    poll_until(client, "ticker", run_id, lambda row: row["status"] == "running", 10)
    first = client.delete(f"/api/v1/jobs/ticker/runs/{run_id}", headers=HEADERS)
    assert first.status_code == 202
    assert first.json()["status"] == "cancelling"
    body = poll_until(
        client, "ticker", run_id, lambda row: row["status"] == "cancelled", 15
    )
    assert body["status"] == "cancelled"
    second = client.delete(f"/api/v1/jobs/ticker/runs/{run_id}", headers=HEADERS)
    assert second.status_code == 200


@pytest.mark.offline
def test_delete_terminal_409(client) -> None:
    created = create(client, "exit3")
    run_id = created["run_id"]
    poll_until(client, "exit3", run_id, lambda row: row["status"] == "failed", 10)
    response = client.delete(f"/api/v1/jobs/exit3/runs/{run_id}", headers=HEADERS)
    assert response.status_code == 409
    assert response.json()["code"] == "run_terminal"


@pytest.mark.offline
def test_delete_admitted_does_not_signal(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JOBS_TOKEN", TOKEN)
    monkeypatch.setenv("JOBS_STATE_DIR", str(tmp_path))

    async def _hold(_args: object, _run_id: str) -> None:
        await asyncio.sleep(30)

    spec = make_spec("hold", "libs/jobs/tests/fixtures/ticker.py", pre_run=_hold)
    app = create_app(load_registry((spec,)))
    with TestClient(app) as client:
        created = create(client, "hold")
        run_id = created["run_id"]
        response = client.delete(f"/api/v1/jobs/hold/runs/{run_id}", headers=HEADERS)
        assert response.status_code == 200
        assert response.json()["status"] == "cancelled"
        status = client.get(f"/api/v1/jobs/hold/runs/{run_id}", headers=HEADERS)
        assert status.json()["status"] == "cancelled"
        assert status.json()["pgid"] is None
