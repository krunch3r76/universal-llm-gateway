"""Determinism fixtures for implement_admission + frontier_consult tests."""

from __future__ import annotations

import os
from pathlib import Path

# a:43747 F-J: import-time GIW registry relay pin (EVENTS_INGEST_SOCK pattern).
_GIW_PYTEST_INERT_URL = os.environ.get("_GIW_PYTEST_INERT_URL", "http://127.0.0.1:9")
if os.environ.get("GIW_REGISTRY_RELAY_ALLOW_LIVE") != "1":
    os.environ.setdefault("GIW_REGISTRY_RELAY_MODE", "pytest_inert")
    os.environ.setdefault("GIT_INTEGRATION_WORKER_URL", _GIW_PYTEST_INERT_URL)

from unittest.mock import AsyncMock, create_autospec  # noqa: E402

import pytest  # noqa: E402

from systems.frontier_consult.skill_suggest_dispatch_config import (  # noqa: E402
    SkillSuggestDispatchConfig,
)


def offline_skill_suggest_dispatch_config(
    **overrides: float,
) -> SkillSuggestDispatchConfig:
    base = {
        "idle_timeout_seconds": 120.0,
        "ack_window_seconds": 3.0,
        "idle_poll_interval_seconds": 0.01,
        "cortex_timeout_seconds": 30.0,
        "mcp_relay_timeout_seconds": 330.0,
        "agent_bus_wait_chunk_seconds": 60.0,
        "agent_bus_client_timeout_seconds": 70.0,
        "agent_bus_max_wait_seconds": 60.0,
        "worker_probe_timeout_seconds": 10.0,
        "worker_dispatch_http_timeout_seconds": 10.0,
        "wait_retry_backoff_seconds": 1.0,
        "worker_outer_timeout_seconds": 1920.0,
    }
    base.update(overrides)
    return SkillSuggestDispatchConfig(**base)


def dispatch_cursor_sdk_generate_mock(**kwargs: object) -> AsyncMock:
    from systems.frontier_consult.cursor_sdk_generate import (
        dispatch_cursor_sdk_generate,
    )

    kwargs.pop("autospec", None)
    return create_autospec(dispatch_cursor_sdk_generate, **kwargs)


def prepare_cursor_sdk_generate_mock(**kwargs: object) -> AsyncMock:
    from systems.frontier_consult.cursor_sdk_generate_prepare import (
        prepare_cursor_sdk_generate,
    )

    kwargs.pop("autospec", None)
    return create_autospec(prepare_cursor_sdk_generate, **kwargs)


@pytest.fixture(autouse=True)
def _stub_skill_suggest_dispatch_config(monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = offline_skill_suggest_dispatch_config()
    monkeypatch.setattr(
        "systems.frontier_consult.skill_suggest_dispatch._dispatch_config",
        lambda: cfg,
    )


@pytest.fixture(autouse=True)
def _reset_drift_gate_state():
    from implement_admission.drift_gates import clear_gate_state_cache

    saved = {k: v for k, v in os.environ.items() if k.startswith("UA_DRIFT_GATE_")}
    for k in saved:
        os.environ.pop(k, None)
    clear_gate_state_cache()
    try:
        yield
    finally:
        for k in list(os.environ):
            if k.startswith("UA_DRIFT_GATE_"):
                os.environ.pop(k, None)
        os.environ.update(saved)
        clear_gate_state_cache()


@pytest.fixture(autouse=True)
def _stub_team_dispatch_admit_pipeline(monkeypatch: pytest.MonkeyPatch) -> None:
    from pipelines.team_dispatch_admit.v1.handlers.test_handlers import (
        run_team_dispatch_admit_for_route_tests,
    )

    monkeypatch.setattr(
        "systems.frontier_consult.team_dispatch_admit_client.run_team_dispatch_admit",
        run_team_dispatch_admit_for_route_tests,
    )


@pytest.fixture(autouse=True)
def house_watch_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    from implement_admission.house_testing import isolate_watch_dir

    return isolate_watch_dir(monkeypatch, tmp_path)


@pytest.fixture
def pinned_cdp_roles(monkeypatch: pytest.MonkeyPatch):
    from implement_admission.house_testing import pin_cdp_roles

    return pin_cdp_roles(monkeypatch)


@pytest.fixture
def admit_other_cursor_models(monkeypatch: pytest.MonkeyPatch) -> None:
    """Rule B: widen the pair allowlist for pool-fence / other-models tests."""
    import cursor_capabilities.cursor_capabilities as cap_mod

    monkeypatch.setattr(
        cap_mod,
        "CURSOR_SDK_ADMITTED_MODELS",
        frozenset(
            {
                "composer-2.5",
                "grok-4.7",
                "claude-sonnet-5",
                "claude-fable-5",
                "claude-fable-5-1",
                "gpt-5.6-terra",
                "gpt-5.6-luna",
                "muse-wonder-1.0",
                "gemini-4-argon",
            }
        ),
    )
