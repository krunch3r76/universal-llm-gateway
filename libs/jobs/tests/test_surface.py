"""Surface header membership. Life is not a member of any production or fixture job."""

from __future__ import annotations

import pytest

from jobs.tests.conftest import HEADERS, create


def _life(extra: dict | None = None) -> dict:
    headers = {"Authorization": HEADERS["Authorization"], "X-ULG-Surface": "life"}
    if extra:
        headers.update(extra)
    return headers


@pytest.mark.offline
def test_surface_absent_is_403(client) -> None:
    response = client.get("/api/v1/jobs", headers={"Authorization": HEADERS["Authorization"]})
    assert response.status_code == 403
    assert response.json()["code"] == "surface_undeclared"


@pytest.mark.offline
def test_life_catalog_is_empty(client) -> None:
    response = client.get("/api/v1/jobs", headers=_life())
    assert response.status_code == 200
    assert response.json()["members"] == []


@pytest.mark.offline
def test_life_member_routes_are_403(client) -> None:
    created = create(client, "exit3")
    run_id = created["run_id"]
    paths = [
        ("POST", "/api/v1/jobs/exit3", {"args": {}}),
        ("GET", f"/api/v1/jobs/exit3/runs/{run_id}", None),
        ("GET", f"/api/v1/jobs/exit3/runs/{run_id}/log", None),
        ("DELETE", f"/api/v1/jobs/exit3/runs/{run_id}", None),
    ]
    for method, path, body in paths:
        response = client.request(method, path, headers=_life(), json=body)
        assert response.status_code == 403, (method, path, response.text)
        assert response.json()["code"] == "surface_not_member"


@pytest.mark.offline
def test_surface_value_rejected(client) -> None:
    response = client.get(
        "/api/v1/jobs",
        headers={"Authorization": HEADERS["Authorization"], "X-ULG-Surface": "guest"},
    )
    assert response.status_code == 422
    assert response.json()["code"] == "surface_invalid"
