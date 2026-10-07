"""Thread delivery posts /turns. A 503 is journaled and retried once."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from jobs import events
from jobs.server import create_app
from jobs.tests.conftest import HEADERS, TOKEN, fixture_specs, poll_until


class _Bus:
    def __init__(self, statuses: list[int]) -> None:
        self.statuses = list(statuses)
        self.posts: list[dict] = []

    def __call__(self, *_args: object, **_kwargs: object) -> _Bus:
        return self

    async def __aenter__(self) -> _Bus:
        return self

    async def __aexit__(self, *_exc: object) -> None:
        return None

    async def post(self, path: str, json: dict, headers: dict) -> object:
        import httpx

        self.posts.append({"path": path, "json": json, "headers": headers})
        status = self.statuses.pop(0)
        return httpx.Response(status, json={"turn_number": 9})


def _app(tmp_path, monkeypatch: pytest.MonkeyPatch, bus: _Bus) -> TestClient:
    monkeypatch.setenv("JOBS_TOKEN", TOKEN)
    monkeypatch.setenv("JOBS_STATE_DIR", str(tmp_path))
    monkeypatch.setenv("AGENT_BUS_TOKEN", "bus-token")
    events.reset_published_events()
    app = create_app(fixture_specs(), client_factory=bus)
    return TestClient(app)


@pytest.mark.offline
def test_thread_post_and_undelivered_then_redelivery(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    bus = _Bus([503, 201])
    with _app(tmp_path, monkeypatch, bus) as client:
        response = client.post(
            "/api/v1/jobs/exit3",
            headers=HEADERS,
            json={"args": {}, "output_contract": "thread", "target_thread": "42"},
        )
        assert response.status_code == 202
        run_id = response.json()["run_id"]
        body = poll_until(
            client,
            "exit3",
            run_id,
            lambda row: row["delivery"] and row["delivery"]["state"] == "undelivered",
            10,
        )
        assert body["status"] == "failed"
        assert bus.posts[0]["json"]["thread"] == "42"
        assert run_id in bus.posts[0]["json"]["body"]
        assert any(event.signal == "jobs.run.undelivered" for event in events.published_events())
    with _app(tmp_path, monkeypatch, bus) as client:
        recovered = poll_until(
            client,
            "exit3",
            run_id,
            lambda row: row["delivery"] and row["delivery"]["state"] == "delivered",
            10,
        )
    assert recovered["delivery"]["turn_number"] == 9
    assert len(bus.posts) == 2
