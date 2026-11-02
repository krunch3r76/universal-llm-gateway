"""Unit tests for call-time GIW URL resolution."""

from __future__ import annotations

import pytest

from giw_endpoint import git_integration_worker_base_url, giw_api_url


@pytest.fixture(autouse=True)
def _clear_giw_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GIT_INTEGRATION_WORKER_URL", raising=False)
    monkeypatch.delenv("GIT_INTEGRATION_WORKER_HOST", raising=False)
    monkeypatch.delenv("GIT_INTEGRATION_WORKER_PORT", raising=False)


def test_base_url_reads_env_after_import(monkeypatch: pytest.MonkeyPatch) -> None:
    import giw_endpoint as mod

    assert mod.git_integration_worker_base_url() == "http://127.0.0.1:8091"
    monkeypatch.setenv("GIT_INTEGRATION_WORKER_URL", "http://example.test:9001/")
    assert git_integration_worker_base_url() == "http://example.test:9001"
    monkeypatch.setenv("GIT_INTEGRATION_WORKER_URL", "http://other.test:8000")
    assert git_integration_worker_base_url() == "http://other.test:8000"


def test_base_url_host_port_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    assert git_integration_worker_base_url() == "http://127.0.0.1:8091"

    monkeypatch.setenv("GIT_INTEGRATION_WORKER_URL", "")
    assert git_integration_worker_base_url() == "http://127.0.0.1:8091"

    monkeypatch.setenv("GIT_INTEGRATION_WORKER_URL", "   ")
    assert git_integration_worker_base_url() == "http://127.0.0.1:8091"

    monkeypatch.delenv("GIT_INTEGRATION_WORKER_URL", raising=False)
    monkeypatch.setenv("GIT_INTEGRATION_WORKER_HOST", "10.0.0.5")
    monkeypatch.setenv("GIT_INTEGRATION_WORKER_PORT", "7777")
    assert git_integration_worker_base_url() == "http://10.0.0.5:7777"

    monkeypatch.setenv("GIT_INTEGRATION_WORKER_URL", "http://explicit.test/")
    assert git_integration_worker_base_url() == "http://explicit.test"
    assert giw_api_url("/health") == "http://explicit.test/health"
    assert giw_api_url("api/v1/git/active-work") == "http://explicit.test/api/v1/git/active-work"


def test_non_integer_port_raises_named_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GIT_INTEGRATION_WORKER_PORT", "abc")
    with pytest.raises(ValueError, match="GIT_INTEGRATION_WORKER_PORT"):
        git_integration_worker_base_url()
    monkeypatch.setenv("GIT_INTEGRATION_WORKER_URL", "http://u.test:1")
    assert git_integration_worker_base_url() == "http://u.test:1"
