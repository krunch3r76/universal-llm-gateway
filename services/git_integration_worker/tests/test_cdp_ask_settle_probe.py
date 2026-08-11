"""Tests for cdp-ask settle probe URL resolution (arc 6655 Rank 1)."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from services.git_integration_worker.cursor_auto.propagation_probe import (
    _fetch_cdp_ask_health,
    resolve_cdp_ask_probe_base_url,
)


@pytest.fixture(autouse=True)
def _clear_project_ask_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PROJECT_ASK_URL", raising=False)


def test_resolve_env_absent_config_present() -> None:
    with patch(
        "scripts.model_manager.ui.controller.service_config.cdp_ask_url_config",
        return_value=("jupiter", 8770, "http://jupiter:8770"),
    ):
        assert resolve_cdp_ask_probe_base_url() == "http://jupiter:8770"


def test_resolve_env_and_config_both_absent() -> None:
    with patch(
        "scripts.model_manager.ui.controller.service_config.cdp_ask_url_config",
        return_value=None,
    ):
        assert resolve_cdp_ask_probe_base_url() is None


def test_resolve_env_set_config_absent() -> None:
    with patch(
        "scripts.model_manager.ui.controller.service_config.cdp_ask_url_config",
        return_value=None,
    ), patch.dict("os.environ", {"PROJECT_ASK_URL": "http://127.0.0.1:8765"}):
        assert resolve_cdp_ask_probe_base_url() == "http://127.0.0.1:8765"


def test_resolve_env_set_and_correct() -> None:
    url = "http://jupiter:8770"
    with patch(
        "scripts.model_manager.ui.controller.service_config.cdp_ask_url_config",
        return_value=("jupiter", 8770, url),
    ), patch.dict("os.environ", {"PROJECT_ASK_URL": url}):
        assert resolve_cdp_ask_probe_base_url() == url


def test_resolve_env_stale_config_authoritative() -> None:
    with patch(
        "scripts.model_manager.ui.controller.service_config.cdp_ask_url_config",
        return_value=("jupiter", 8770, "http://jupiter:8770"),
    ), patch.dict("os.environ", {"PROJECT_ASK_URL": "http://stale:9999"}):
        assert resolve_cdp_ask_probe_base_url() == "http://jupiter:8770"


def test_fetch_cdp_ask_health_env_absent_uses_config() -> None:
    health = {
        "status": "ok",
        "code_version": "abc",
        "pid": 1,
    }
    with patch(
        "scripts.model_manager.ui.controller.service_config.cdp_ask_url_config",
        return_value=("jupiter", 8770, "http://jupiter:8770"),
    ), patch(
        "services.git_integration_worker.cursor_auto.propagation_probe._fetch_health_at_base",
        return_value=health,
    ) as fetch_mock:
        assert _fetch_cdp_ask_health() == health
    fetch_mock.assert_called_once_with("http://jupiter:8770")


def test_fetch_cdp_ask_health_unresolved_returns_none() -> None:
    with patch(
        "scripts.model_manager.ui.controller.service_config.cdp_ask_url_config",
        return_value=None,
    ):
        assert _fetch_cdp_ask_health() is None


def test_defer_tokens_for_ready_wait_are_distinct() -> None:
    from charter_runner_store.propagation_terminal import (
        _DEFER_AFTER_DRAIN,
        _DEFER_READY_TIMEOUT,
        _DEFER_READY_WAIT,
    )
    from scripts.model_manager.ui.controller.propagation_ready_join import (
        DEFER_READY_TIMEOUT,
        DEFER_READY_WAIT,
        DEFER_UNREACHABLE,
    )

    tokens = {
        _DEFER_AFTER_DRAIN,
        _DEFER_READY_WAIT,
        _DEFER_READY_TIMEOUT,
        DEFER_UNREACHABLE,
        DEFER_READY_WAIT,
        DEFER_READY_TIMEOUT,
    }
    assert len(tokens) == 3
    assert _DEFER_AFTER_DRAIN == "proof_pending_after_drain"
    assert _DEFER_READY_WAIT == "proof_pending_ready_wait"
    assert _DEFER_READY_TIMEOUT == "proof_pending_ready_timeout"
