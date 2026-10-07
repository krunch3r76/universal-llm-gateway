"""Log offset bounds. Bytes past the file, and negative offsets, are 416."""

from __future__ import annotations

import pytest

from jobs.tests.conftest import HEADERS, create, poll_until


@pytest.mark.offline
def test_offset_bounds(client) -> None:
    created = create(client, "exit3")
    run_id = created["run_id"]
    poll_until(client, "exit3", run_id, lambda row: row["status"] == "failed", 10)
    path = f"/api/v1/jobs/exit3/runs/{run_id}/log"
    negative = client.get(path, headers=HEADERS, params={"offset": -1})
    assert negative.status_code == 416
    assert negative.json()["code"] == "offset_out_of_range"
    huge = client.get(path, headers=HEADERS, params={"offset": 10_000_000})
    assert huge.status_code == 416
    ok = client.get(path, headers=HEADERS, params={"offset": 0})
    assert ok.status_code == 200
    assert "X-Log-Next-Offset" in ok.headers
