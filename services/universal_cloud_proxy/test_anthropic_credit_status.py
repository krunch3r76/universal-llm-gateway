"""Status route behavior for anthropic credit meter."""

from __future__ import annotations

from pathlib import Path

import pytest
from anthropic_credit.config import load_credit_config
from anthropic_credit.ledger_init import init_ledger
from anthropic_credit.policy import CreditPolicy, set_policy
from fastapi.testclient import TestClient

from services.universal_cloud_proxy.cloud_proxy import app

pytestmark = pytest.mark.offline


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


def _write_config(tmp_path: Path, ledger_path: Path, **overrides: object) -> Path:
    from anthropic_credit.test_config import _write_minimal_config

    return _write_minimal_config(
        tmp_path,
        ledger_path=str(ledger_path),
        grant_usd=1_000_000,
        hard_stop_usd=999_999,
        mcp_connector_stop_usd=999_998,
        **overrides,
    )


@pytest.fixture
def config_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    missing = tmp_path / "missing.sqlite"
    cfg_path = _write_config(tmp_path, missing)
    monkeypatch.setenv("ANTHROPIC_CREDIT_CONFIG_PATH", str(cfg_path))
    yield cfg_path, missing


def test_status_missing_ledger_returns_200_unhealthy(
    client: TestClient, config_env: tuple[Path, Path]
) -> None:
    cfg_path, missing = config_env
    set_policy(None)
    set_policy(CreditPolicy(load_credit_config(cfg_path)))
    resp = client.get("/internal/anthropic-credit/status")
    assert resp.status_code == 200
    body = resp.json()
    assert body["healthy"] is False
    assert "unavailable_reason" in body
    assert body["ledger_path"] == str(missing.resolve())


def test_status_healthy_includes_instance_fields(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    ledger = tmp_path / "ledger.sqlite"
    cfg_path = _write_config(tmp_path, ledger)
    monkeypatch.setenv("ANTHROPIC_CREDIT_CONFIG_PATH", str(cfg_path))
    cfg = load_credit_config(cfg_path)
    init_ledger(cfg)
    set_policy(None)
    set_policy(CreditPolicy(cfg))
    resp = client.get("/internal/anthropic-credit/status")
    assert resp.status_code == 200
    body = resp.json()
    assert body["healthy"] is True
    assert body["ledger_path"] == str(ledger.resolve())
    assert body["ledger_instance_id"]
    assert "available_balance_as_of" in body
