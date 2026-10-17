"""Status route behavior for anthropic credit meter."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest
from anthropic_credit.config import load_credit_config
from anthropic_credit.ledger_init import init_ledger
from anthropic_credit.policy import Admit, CreditPolicy, set_policy
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
    assert body["ledger_instance_id"] is None
    assert not missing.is_file()


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


def test_status_corrupt_ledger_returns_ledger_corrupt(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import sqlite3

    ledger = tmp_path / "ledger.sqlite"
    cfg_path = _write_config(tmp_path, ledger)
    monkeypatch.setenv("ANTHROPIC_CREDIT_CONFIG_PATH", str(cfg_path))
    cfg = load_credit_config(cfg_path)
    init_ledger(cfg)
    policy = CreditPolicy(cfg)
    from datetime import UTC, datetime

    from anthropic_credit.policy import Admit

    t0 = datetime(2026, 10, 17, 15, 0, 0, tzinfo=UTC)
    assert isinstance(
        policy.admit(
            "corrupt-row",
            "anthropic/claude-sonnet-5",
            {
                "model": "claude-sonnet-5",
                "max_tokens": 100,
                "messages": [{"role": "user", "content": "x"}],
            },
            token_count=0,
            now=t0,
        ),
        Admit,
    )
    with sqlite3.connect(str(ledger)) as conn:
        conn.execute(
            "UPDATE entries SET created_at = 'not-a-timestamp' WHERE request_id = 'corrupt-row'"
        )
    set_policy(None)
    set_policy(policy)
    resp = client.get("/internal/anthropic-credit/status")
    assert resp.status_code == 200
    body = resp.json()
    assert body["healthy"] is False
    assert body["unavailable_reason"] == "ledger_corrupt"


def test_status_value_error_from_reserve_created_at_returns_ledger_corrupt(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    ledger = tmp_path / "ledger.sqlite"
    cfg_path = _write_config(tmp_path, ledger)
    monkeypatch.setenv("ANTHROPIC_CREDIT_CONFIG_PATH", str(cfg_path))
    cfg = load_credit_config(cfg_path)
    init_ledger(cfg)
    policy = CreditPolicy(cfg)
    t0 = datetime(2026, 10, 17, 15, 0, 0, tzinfo=UTC)
    assert isinstance(
        policy.admit(
            "value-error-reserve",
            "anthropic/claude-sonnet-5",
            {
                "model": "claude-sonnet-5",
                "max_tokens": 100,
                "messages": [{"role": "user", "content": "x"}],
            },
            token_count=0,
            now=t0,
        ),
        Admit,
    )
    policy.commit(
        "value-error-reserve",
        {
            "input_tokens": 10,
            "cache_creation_input_tokens": 0,
            "cache_read_input_tokens": 0,
            "output_tokens": 10,
        },
        now=t0,
    )
    with sqlite3.connect(str(ledger)) as conn:
        conn.execute(
            """
            UPDATE entries SET created_at = 'not-a-timestamp'
            WHERE request_id = 'value-error-reserve' AND kind = 'reserve'
            """
        )
    set_policy(None)
    set_policy(policy)
    resp = client.get("/internal/anthropic-credit/status")
    assert resp.status_code == 200
    body = resp.json()
    assert body["healthy"] is False
    assert body["unavailable_reason"] == "ledger_corrupt"


def test_status_uses_policy_cached_config_not_fresh_load(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    ledger = tmp_path / "ledger.sqlite"
    cfg_path = _write_config(
        tmp_path,
        ledger,
        available_balance={
            "usd": 200,
            "as_of": "2026-01-01T00:00:00+00:00",
            "owner": "kaywan",
        },
    )
    monkeypatch.setenv("ANTHROPIC_CREDIT_CONFIG_PATH", str(cfg_path))
    cfg = load_credit_config(cfg_path)
    init_ledger(cfg)
    set_policy(None)
    set_policy(CreditPolicy(cfg))
    resp1 = client.get("/internal/anthropic-credit/status")
    assert resp1.json()["available_balance_as_of"] == "2026-01-01T00:00:00+00:00"
    cfg_path.write_text(
        cfg_path.read_text(encoding="utf-8").replace(
            "2026-01-01T00:00:00+00:00", "2026-12-31T00:00:00+00:00"
        ),
        encoding="utf-8",
    )
    resp2 = client.get("/internal/anthropic-credit/status")
    assert resp2.json()["available_balance_as_of"] == "2026-01-01T00:00:00+00:00"


def test_status_future_available_balance_as_of_unhealthy(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    ledger = tmp_path / "ledger.sqlite"
    future = datetime(2099, 1, 1, 0, 0, 0, tzinfo=UTC)
    cfg_path = _write_config(
        tmp_path,
        ledger,
        available_balance={
            "usd": 200,
            "as_of": future.isoformat(),
            "owner": "kaywan",
        },
    )
    monkeypatch.setenv("ANTHROPIC_CREDIT_CONFIG_PATH", str(cfg_path))
    cfg = load_credit_config(cfg_path)
    init_ledger(cfg)
    set_policy(None)
    set_policy(CreditPolicy(cfg))
    resp = client.get("/internal/anthropic-credit/status")
    assert resp.status_code == 200
    body = resp.json()
    assert body["healthy"] is False
    assert body["unavailable_reason"] == "balance_as_of_in_future"
