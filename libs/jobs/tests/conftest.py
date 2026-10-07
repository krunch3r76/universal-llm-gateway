"""Shared jobs HTTP client. The production registry is not this fixture.

Fake jobs point at repo fixture scripts. ``JOBS_TOKEN`` is ``test-token``.
Callers send ``X-ULG-Surface: code`` unless a test overrides the header.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from datetime import date

import pytest
from fastapi.testclient import TestClient
from pydantic import BaseModel, ConfigDict

from jobs.server import create_app
from jobs.spec import GraduationTarget, JobSpec, load_registry

TOKEN = "test-token"
HEADERS = {"Authorization": f"Bearer {TOKEN}", "X-ULG-Surface": "code"}


class EmptyArgs(BaseModel):
    """Args model with no fields. Used only by test jobs."""

    model_config = ConfigDict(extra="forbid")


def _argv(_args: BaseModel) -> list[str]:
    return []


def make_spec(
    name: str,
    executable: str,
    *,
    idle_seconds: int = 30,
    pre_run: Callable | None = None,
) -> JobSpec:
    """Build one test JobSpec that load_registry will accept."""
    return JobSpec(
        name=name,
        description=f"Test job {name} for the jobs satellite suite.",
        surfaces=frozenset({"code"}),
        args_model=EmptyArgs,
        argv=_argv,
        executable=executable,
        idle_seconds=idle_seconds,
        graduates_to=GraduationTarget(
            kind="manage_lifecycle",
            owner="watch",
            note="Test fixture graduation target for the jobs suite.",
        ),
        sunset=date(2027, 4, 1),
        handle=f"capability:jobs/{name}",
        pre_run=pre_run,
    )


def fixture_specs() -> tuple[JobSpec, ...]:
    """Ticker, silent, exit-3, and chatter. Exactly the registry cap."""
    root = "libs/jobs/tests/fixtures"
    return load_registry(
        (
            make_spec("ticker", f"{root}/ticker.py", idle_seconds=30),
            make_spec("silent", f"{root}/silent.py", idle_seconds=2),
            make_spec("exit3", f"{root}/exit3.py", idle_seconds=30),
            make_spec("chatter", f"{root}/chatter.py", idle_seconds=2),
        )
    )


@pytest.fixture
def client(tmp_path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("JOBS_TOKEN", TOKEN)
    monkeypatch.setenv("JOBS_STATE_DIR", str(tmp_path))
    monkeypatch.setenv("JOBS_EVENT_SINK", str(tmp_path / "events.ndjson"))
    app = create_app(fixture_specs())
    with TestClient(app) as test_client:
        yield test_client


def create(client: TestClient, job: str, body: dict | None = None) -> dict:
    """POST a run and return the 202 body. Fails the test when create is refused."""
    response = client.post(f"/api/v1/jobs/{job}", headers=HEADERS, json=body or {"args": {}})
    assert response.status_code == 202, response.text
    return response.json()


def poll_until(client: TestClient, job: str, run_id: str, pred: Callable[[dict], bool], timeout: float) -> dict:
    """GET status until pred holds or the timeout elapses."""
    deadline = time.monotonic() + timeout
    last: dict = {}
    while time.monotonic() < deadline:
        response = client.get(f"/api/v1/jobs/{job}/runs/{run_id}", headers=HEADERS)
        assert response.status_code == 200, response.text
        last = response.json()
        if pred(last):
            return last
        time.sleep(0.05)
    raise AssertionError(last)
