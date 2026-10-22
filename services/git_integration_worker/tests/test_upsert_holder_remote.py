"""Unit tests for ``upsert_holder_remote`` HTTP outcome classification."""

from __future__ import annotations

import json
import urllib.error
from io import BytesIO
from unittest.mock import MagicMock, patch

import pytest

from services.git_integration_worker.cse_session_holders import (
    RemoteUpsert,
    upsert_holder_remote,
)


def _http_error(*, code: int, body: dict) -> urllib.error.HTTPError:
    encoded = json.dumps(body).encode()
    return urllib.error.HTTPError(
        url="http://127.0.0.1:8091/api/v1/git/admin/cse-holder/upsert",
        code=code,
        msg="Error",
        hdrs={},
        fp=BytesIO(encoded),
    )


@pytest.mark.offline
def test_upsert_holder_remote_409_lane_conflict() -> None:
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
def test_upsert_holder_remote_urlerror_unavailable() -> None:
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
