"""Offline tests for fleet cookie backup / rehydrate (R2)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from claude_bundles import cdp_cookie_seed

pytestmark = pytest.mark.offline

_COOKIE = {
    "name": "sessionKey",
    "value": "abc",
    "domain": ".claude.ai",
    "path": "/",
    "secure": True,
    "httpOnly": True,
}


def test_write_fleet_cookie_backup_atomic(tmp_path: Path, monkeypatch) -> None:
    backup = tmp_path / "claude-ai-cookies-backup-latest.json"
    monkeypatch.setattr(
        cdp_cookie_seed,
        "read_session_cookies",
        lambda port, **kw: [_COOKIE],
    )

    count = cdp_cookie_seed.write_fleet_cookie_backup(
        9222, backup_path=backup, domain_suffix="claude.ai"
    )

    assert count == 1
    assert backup.is_file()
    payload = json.loads(backup.read_text())
    assert payload["cookies"] == [_COOKIE]
    assert payload["source_port"] == 9222


def test_rehydrate_fleet_from_backup(tmp_path: Path, monkeypatch) -> None:
    backup = tmp_path / "claude-ai-cookies-backup-latest.json"
    backup.write_text(
        json.dumps({"cookies": [_COOKIE], "source_port": 9222}) + "\n",
        encoding="utf-8",
    )
    calls: list[tuple] = []

    def _set(port: int, method: str, params: dict | None = None) -> dict:
        calls.append((port, method, params))
        return {}

    monkeypatch.setattr(cdp_cookie_seed, "_cdp_call", _set)

    count = cdp_cookie_seed.rehydrate_fleet_from_backup(
        9222, backup_path=backup, domain_suffix="claude.ai"
    )

    assert count == 1
    assert calls == [(9222, "Storage.setCookies", {"cookies": [_COOKIE]})]


def test_fleet_cookie_rehydrate_fresh(monkeypatch) -> None:
    monkeypatch.setattr(
        cdp_cookie_seed,
        "write_fleet_cookie_backup",
        lambda *a, **k: 2,
    )

    assert cdp_cookie_seed.fleet_cookie_rehydrate(9222) == "fresh"


def test_fleet_cookie_rehydrate_restored(monkeypatch) -> None:
    def _write(*a, **k):
        raise cdp_cookie_seed.CookieSeedError("signed out")

    monkeypatch.setattr(cdp_cookie_seed, "write_fleet_cookie_backup", _write)
    monkeypatch.setattr(
        cdp_cookie_seed,
        "rehydrate_fleet_from_backup",
        lambda *a, **k: 3,
    )

    assert cdp_cookie_seed.fleet_cookie_rehydrate(9222) == "restored"


def test_fleet_cookie_rehydrate_signed_out(monkeypatch) -> None:
    def _fail(*a, **k):
        raise cdp_cookie_seed.CookieSeedError("no backup")

    monkeypatch.setattr(cdp_cookie_seed, "write_fleet_cookie_backup", _fail)
    monkeypatch.setattr(cdp_cookie_seed, "rehydrate_fleet_from_backup", _fail)

    assert cdp_cookie_seed.fleet_cookie_rehydrate(9222) == "signed_out"


def test_rehydrate_rejects_missing_backup(tmp_path: Path) -> None:
    with pytest.raises(cdp_cookie_seed.CookieSeedError, match="no cookie backup"):
        cdp_cookie_seed.rehydrate_fleet_from_backup(
            9222, backup_path=tmp_path / "missing.json"
        )
