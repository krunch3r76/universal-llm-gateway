"""Pytest fence for ``release_holder_remote`` / ``upsert_holder_remote`` HTTP writes."""

from __future__ import annotations

from contextlib import contextmanager
from unittest.mock import MagicMock, patch

import pytest

from services.git_integration_worker.cse_session_holders import (
    RemoteUpsert,
    release_holder_remote,
    upsert_holder_remote,
)

_CHAT = "https://claude.ai/cowork/cse_guard_test"


@contextmanager
def _mock_urlopen_ok():
    mock_resp = MagicMock()
    mock_resp.status = 200
    mock_resp.__enter__ = MagicMock(return_value=mock_resp)
    mock_resp.__exit__ = MagicMock(return_value=False)
    with patch(
        "services.git_integration_worker.cse_session_holders.urllib.request.urlopen",
        return_value=mock_resp,
    ) as mock_urlopen:
        yield mock_urlopen


@pytest.mark.offline
def test_blocked_upsert_skips_urlopen() -> None:
    with _mock_urlopen_ok() as mock_urlopen:
        result = upsert_holder_remote(chat_url=_CHAT, registration_id="reg-1")

    assert result is RemoteUpsert.UNAVAILABLE
    mock_urlopen.assert_not_called()


@pytest.mark.offline
def test_blocked_release_skips_urlopen() -> None:
    with _mock_urlopen_ok() as mock_urlopen:
        ok = release_holder_remote(chat_url=_CHAT, registration_id="reg-1")

    assert ok is False
    mock_urlopen.assert_not_called()


@pytest.mark.offline
def test_opt_in_upsert_reaches_urlopen(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ULG_ALLOW_REMOTE_HOLDER_WRITE_UNDER_PYTEST", "1")
    with _mock_urlopen_ok() as mock_urlopen:
        result = upsert_holder_remote(chat_url=_CHAT, registration_id="reg-1")

    assert result is RemoteUpsert.OK
    assert mock_urlopen.call_count == 1


@pytest.mark.offline
def test_opt_in_release_reaches_urlopen(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ULG_ALLOW_REMOTE_HOLDER_WRITE_UNDER_PYTEST", "1")
    with _mock_urlopen_ok() as mock_urlopen:
        ok = release_holder_remote(chat_url=_CHAT, registration_id="reg-1")

    assert ok is True
    assert mock_urlopen.call_count == 1


@pytest.mark.offline
def test_without_pytest_marker_reaches_urlopen(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    with _mock_urlopen_ok() as mock_urlopen:
        result = upsert_holder_remote(chat_url=_CHAT, registration_id="reg-1")

    assert result is RemoteUpsert.OK
    assert mock_urlopen.call_count == 1
