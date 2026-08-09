"""fetch_job_state retry ladder — transport failures retry; definitive answers do not."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import httpx

from tools.agent_bus.job_state_client import fetch_job_state


def test_fetch_job_state_retries_unreachable_then_hits() -> None:
    hit = MagicMock()
    hit.status_code = 200
    hit.content = b'{"ok":true,"found":true,"job":{"job_id":"j1"}}'
    hit.json.return_value = {"ok": True, "found": True, "job": {"job_id": "j1"}}

    client = MagicMock()
    client.__enter__.return_value = client
    client.__exit__.return_value = None
    client.get.side_effect = [httpx.ReadTimeout("timed out"), hit]

    with (
        patch("tools.agent_bus.job_state_client.httpx.Client", return_value=client),
        patch("tools.agent_bus.job_state_client.time.sleep") as sleep,
    ):
        result = fetch_job_state(job_id="j1", backoff_s=(0.0, 0.0))

    assert result["found"] is True
    assert result["reason"] == "ok"
    assert result["attempts"] == 2
    assert client.get.call_count == 2
    assert sleep.call_count == 1


def test_fetch_job_state_not_found_does_not_retry() -> None:
    miss = MagicMock()
    miss.status_code = 200
    miss.content = b'{"ok":true,"found":false,"job":null}'
    miss.json.return_value = {"ok": True, "found": False, "job": None}

    client = MagicMock()
    client.__enter__.return_value = client
    client.__exit__.return_value = None
    client.get.return_value = miss

    with patch("tools.agent_bus.job_state_client.httpx.Client", return_value=client):
        result = fetch_job_state(job_id="missing")

    assert result["found"] is False
    assert result["reason"] == "not_found"
    assert result["attempts"] == 1
    assert client.get.call_count == 1


def test_fetch_job_state_legacy_timeout_s_is_single_shot() -> None:
    client = MagicMock()
    client.__enter__.return_value = client
    client.__exit__.return_value = None
    client.get.side_effect = httpx.ReadTimeout("timed out")

    with patch("tools.agent_bus.job_state_client.httpx.Client", return_value=client):
        result = fetch_job_state(job_id="j1", timeout_s=3.0)

    assert result["reason"] == "job_state_unreachable"
    assert result["attempts"] == 1
    assert client.get.call_count == 1


def test_fetch_job_state_missing_key() -> None:
    result = fetch_job_state()
    assert result["reason"] == "missing_key"
    assert result["found"] is False
