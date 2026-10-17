from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _clear_credit_config_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ANTHROPIC_CREDIT_CONFIG_PATH", raising=False)
