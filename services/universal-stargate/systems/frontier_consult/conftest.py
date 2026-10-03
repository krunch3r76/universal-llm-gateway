"""Determinism fixtures for the implement_admission unit suite.

implement_admission.drift_gates.gate_state is a process-global cache; tests mutate
it (and UA_DRIFT_GATE_* env). This autouse fixture clears the cache before and after
every test so on-seat order is irrelevant (the MCP quality gate runs them in one
process). Snapshot/restore env rather than hard-delete so intentional per-test
patch.dict usage is untouched.
"""

from __future__ import annotations

import os
from unittest.mock import AsyncMock

import pytest

from systems.frontier_consult.skill_suggest_dispatch_config import (
    SkillSuggestDispatchConfig,
)


def offline_skill_suggest_dispatch_config(
    **overrides: float,
) -> SkillSuggestDispatchConfig:
    """Hermetic timeouts when skill-suggest-rank pipeline YAML is archived."""
    base = dict(
        idle_timeout_seconds=120.0,
        ack_window_seconds=3.0,
        idle_poll_interval_seconds=0.01,
        cortex_timeout_seconds=30.0,
        mcp_relay_timeout_seconds=330.0,
        agent_bus_wait_chunk_seconds=60.0,
        agent_bus_client_timeout_seconds=70.0,
        agent_bus_max_wait_seconds=60.0,
        worker_probe_timeout_seconds=10.0,
        worker_dispatch_http_timeout_seconds=10.0,
        wait_retry_backoff_seconds=1.0,
        worker_outer_timeout_seconds=1920.0,
    )
    base.update(overrides)
    return SkillSuggestDispatchConfig(**base)


def dispatch_cursor_sdk_generate_mock(**kwargs: object) -> AsyncMock:
    from unittest.mock import create_autospec

    from systems.frontier_consult.cursor_sdk_generate import (
        dispatch_cursor_sdk_generate,
    )

    # Callers pass autospec=True so the test file names the constraint.
    # create_autospec is what actually binds the real signature (contract=).
    kwargs.pop("autospec", None)
    return create_autospec(dispatch_cursor_sdk_generate, **kwargs)


def prepare_cursor_sdk_generate_mock(**kwargs: object) -> AsyncMock:
    from unittest.mock import create_autospec

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
