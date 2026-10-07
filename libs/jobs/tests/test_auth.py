"""Bearer gate. Missing and wrong tokens are 401; an unset token is 500."""

from __future__ import annotations

import pytest

from jobs.tests.conftest import HEADERS


@pytest.mark.offline
def test_missing_bearer_is_401(client) -> None:
    response = client.get("/api/v1/jobs", headers={"X-ULG-Surface": "code"})
    assert response.status_code == 401
    assert response.json()["code"] == "unauthorized"


@pytest.mark.offline
def test_wrong_bearer_is_401(client) -> None:
    response = client.get(
        "/api/v1/jobs",
        headers={"Authorization": "Bearer nope", "X-ULG-Surface": "code"},
    )
    assert response.status_code == 401


@pytest.mark.offline
def test_unset_token_is_500(client, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("JOBS_TOKEN", raising=False)
    response = client.get("/api/v1/jobs", headers=HEADERS)
    assert response.status_code == 500
    assert response.json()["code"] == "jobs_token_unconfigured"
