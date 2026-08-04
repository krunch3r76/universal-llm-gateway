"""M365 mailbox resolution for email dispatch."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from email_routing.m365_mailbox import (
    m365_account_required_error,
    resolve_m365_account,
)


def test_resolve_single_default_account(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    registry = tmp_path / "m365_accounts.yaml"
    registry.write_text(
        yaml.dump({"accounts": {"kaywan@askapharmd.me": {"fetch_path": "headless_export"}}}),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "email_routing.surface_guard._M365_REGISTRY",
        registry,
    )
    assert resolve_m365_account() == "kaywan@askapharmd.me"


def test_resolve_explicit_account(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    registry = tmp_path / "m365_accounts.yaml"
    registry.write_text(
        yaml.dump(
            {
                "accounts": {
                    "a@example.com": {},
                    "b@example.com": {},
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr("email_routing.surface_guard._M365_REGISTRY", registry)
    assert resolve_m365_account(account="b@example.com") == "b@example.com"
    assert resolve_m365_account() is None


def test_account_required_error_lists_upns(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    registry = tmp_path / "m365_accounts.yaml"
    registry.write_text(
        yaml.dump({"accounts": {"a@x.com": {}, "b@x.com": {}}}),
        encoding="utf-8",
    )
    monkeypatch.setattr("email_routing.surface_guard._M365_REGISTRY", registry)
    err = m365_account_required_error()
    assert err["error"] == "account_required"
    assert set(err["m365_accounts"]) == {"a@x.com", "b@x.com"}
