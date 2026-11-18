"""Unit tests for ``upsert_holder_remote`` HTTP outcome classification."""

from __future__ import annotations

import json
import sys
import urllib.error
from io import BytesIO
from unittest.mock import patch

import pytest

from services.git_integration_worker.cse_session_holders import (
    RemoteUpsert,
    upsert_holder_remote,
)


@pytest.fixture(autouse=True)
def _redirect_background_uds_publishers(
    _events_sink_default_off_live: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for mod_name in (
        "agent_bus_store.events.publisher",
        "libs.agent_bus_store.events.publisher",
        "cortex_store.event_publisher",
        "libs.cortex_store.event_publisher",
        "mcp_events",
    ):
        mod = sys.modules.get(mod_name)
        pub = getattr(mod, "_publisher", None) if mod is not None else None
        if pub is not None and hasattr(pub, "_sock_path"):
            monkeypatch.setattr(pub, "_sock_path", _events_sink_default_off_live)
    from services.git_integration_worker.tests.events_sink_hermetic import (
        live_connect_attempts,
    )

    live_connect_attempts.clear()


def _http_error(*, code: int, body: dict) -> urllib.error.HTTPError:
    encoded = json.dumps(body).encode()
    return urllib.error.HTTPError(
        url="http://127.0.0.1:8091/api/v1/git/admin/cse-holder/upsert",
        code=code,
        msg="Error",
        hdrs={},  # type: ignore[arg-type]
        fp=BytesIO(encoded),
    )


@pytest.mark.offline
def test_upsert_holder_remote_409_lane_conflict(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ULG_ALLOW_REMOTE_HOLDER_WRITE_UNDER_PYTEST", "1")
    conflict_body = {
        "detail": {
            "reason": "lane_conflict",
            "holder_id": "cse_abc",
            "current_lane": "19133",
            "requested_lane": "19167",
        }
    }
    with patch(
        "services.git_integration_worker.cse_session_holders.urllib.request.urlopen",
        side_effect=_http_error(code=409, body=conflict_body),
    ) as mock_urlopen:
        result = upsert_holder_remote(
            chat_url="https://claude.ai/cowork/cse_abc",
            registration_id="reg-1",
            lane_thread_id="19167",
        )

    assert result is RemoteUpsert.LANE_CONFLICT
    assert mock_urlopen.call_count == 1


@pytest.mark.offline
def test_upsert_holder_remote_urlerror_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ULG_ALLOW_REMOTE_HOLDER_WRITE_UNDER_PYTEST", "1")
    with patch(
        "services.git_integration_worker.cse_session_holders.urllib.request.urlopen",
        side_effect=urllib.error.URLError("connection refused"),
    ) as mock_urlopen:
        result = upsert_holder_remote(
            chat_url="https://claude.ai/cowork/cse_abc",
            registration_id="reg-1",
        )

    assert result is RemoteUpsert.UNAVAILABLE
    assert mock_urlopen.call_count == 1


@pytest.mark.offline
def test_upsert_holder_remote_422_typed_reason_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ULG_ALLOW_REMOTE_HOLDER_WRITE_UNDER_PYTEST", "1")
    refused_body = {
        "detail": {
            "reason": "houseless_registration",
            "holder_id": "chat_2762063d-2966-4859-a3ce-23831c6e0c86",
            "registration_id": "reg-hl",
        }
    }
    with patch(
        "services.git_integration_worker.cse_session_holders.urllib.request.urlopen",
        side_effect=_http_error(code=422, body=refused_body),
    ):
        result = upsert_holder_remote(
            chat_url="https://claude.ai/chat/2762063d-2966-4859-a3ce-23831c6e0c86",
            registration_id="reg-hl",
        )
    assert result is RemoteUpsert.REFUSED


@pytest.mark.offline
def test_upsert_holder_remote_422_untyped_and_500_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ULG_ALLOW_REMOTE_HOLDER_WRITE_UNDER_PYTEST", "1")
    with patch(
        "services.git_integration_worker.cse_session_holders.urllib.request.urlopen",
        side_effect=_http_error(code=422, body={"detail": "validation error"}),
    ):
        untyped = upsert_holder_remote(
            chat_url="https://claude.ai/cowork/cse_abc",
            registration_id="reg-1",
        )
    assert untyped is RemoteUpsert.UNAVAILABLE

    with patch(
        "services.git_integration_worker.cse_session_holders.urllib.request.urlopen",
        side_effect=_http_error(code=500, body={"detail": "internal error"}),
    ):
        server_err = upsert_holder_remote(
            chat_url="https://claude.ai/cowork/cse_abc",
            registration_id="reg-1",
        )
    assert server_err is RemoteUpsert.UNAVAILABLE
