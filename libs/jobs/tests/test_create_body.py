"""Create-body key and thread-contract refusals."""

from __future__ import annotations

import pytest

from jobs.tests.conftest import HEADERS

_FORBIDDEN = ("command", "path", "argv", "shell", "script", "cwd")


@pytest.mark.offline
@pytest.mark.parametrize("field", _FORBIDDEN)
def test_forbidden_top_level_keys(client, field: str) -> None:
    response = client.post(
        "/api/v1/jobs/ticker",
        headers=HEADERS,
        json={"args": {}, field: "x"},
    )
    assert response.status_code == 422
    body = response.json()
    assert body["code"] == "body_field_forbidden"
    assert body["data"]["field"] == field
    assert body["source"] == "jobs"


@pytest.mark.offline
def test_thread_requires_target(client) -> None:
    response = client.post(
        "/api/v1/jobs/ticker",
        headers=HEADERS,
        json={"args": {}, "output_contract": "thread"},
    )
    assert response.status_code == 422
    assert response.json()["code"] == "target_thread_required"


@pytest.mark.offline
def test_inline_rejects_target(client) -> None:
    response = client.post(
        "/api/v1/jobs/ticker",
        headers=HEADERS,
        json={"args": {}, "output_contract": "inline", "target_thread": "12"},
    )
    assert response.status_code == 422
    assert response.json()["code"] == "target_thread_requires_thread_contract"


@pytest.mark.offline
def test_target_thread_format(client) -> None:
    response = client.post(
        "/api/v1/jobs/ticker",
        headers=HEADERS,
        json={"args": {}, "output_contract": "thread", "target_thread": "nope"},
    )
    assert response.status_code == 422
    assert response.json()["code"] == "target_thread_invalid"
