"""Production registry cap, names, graduation, and sunset."""

from __future__ import annotations

from datetime import date

import pytest
from fastapi.testclient import TestClient

from jobs.server import create_app
from jobs.spec import JobSpecInvalidError, RegistryUnboundedError, load_registry
from jobs.tests.conftest import HEADERS, TOKEN, make_spec
from scripts.model_manager.ui.api_dispatch import VALID_SERVICES

_NAMES = {"ide-hop", "bus-reply-watch", "claude-ai-sync"}


@pytest.mark.offline
def test_production_names_and_graduation() -> None:
    specs = load_registry()
    assert {spec.name for spec in specs} == _NAMES
    for spec in specs:
        assert spec.sunset == date(2027, 4, 1)
        assert spec.graduates_to.kind in {"satellite_route", "manage_lifecycle"}
        if spec.graduates_to.kind == "satellite_route":
            assert spec.graduates_to.owner in set(VALID_SERVICES) | {"web_fetcher"}


@pytest.mark.offline
def test_fifth_job_raises() -> None:
    specs = [
        make_spec(f"job-{index}", "scripts/ingest-article")
        for index in range(5)
    ]
    with pytest.raises(RegistryUnboundedError):
        load_registry(tuple(specs))


@pytest.mark.offline
def test_name_pattern() -> None:
    spec = make_spec("ticker", "scripts/ingest-article")
    bad = spec.model_copy(update={"name": "Bad.Name", "handle": "capability:jobs/Bad.Name"})
    with pytest.raises(JobSpecInvalidError):
        load_registry((bad,))


@pytest.mark.offline
def test_sunset_410(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JOBS_TOKEN", TOKEN)
    monkeypatch.setenv("JOBS_STATE_DIR", str(tmp_path))
    app = create_app(today=lambda: date(2027, 4, 2))
    with TestClient(app) as client:
        gone = client.post(
            "/api/v1/jobs/article-fetch",
            headers=HEADERS,
            json={"args": {"url": "https://example.com/a.pdf"}},
        )
        sunset = client.post(
            "/api/v1/jobs/ide-hop",
            headers=HEADERS,
            json={
                "args": {
                    "row": "now",
                    "transcript_id": "11111111-1111-1111-1111-111111111111",
                }
            },
        )
    assert gone.status_code == 404
    assert gone.json()["code"] == "job_not_found"
    assert sunset.status_code == 410
    assert sunset.json()["code"] == "job_sunset"
