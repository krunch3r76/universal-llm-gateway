"""Tests for email surface fail-closed guards."""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from email_routing.surface_guard import (
    apply_indeterminate_if_degraded,
    check_mailbox_surface,
    wrong_surface_response,
)


@pytest.fixture(autouse=True)
def _clear_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("EMAIL_BRIDGE_ACCOUNTS_JSON", raising=False)
    monkeypatch.delenv("IMAP_ACCOUNTS_JSON", raising=False)
    monkeypatch.delenv("IMAP_USER", raising=False)


def test_wrong_surface_for_m365_mailbox(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    registry = tmp_path / "m365_accounts.yaml"
    registry.write_text(
        "accounts:\n  user@example.com:\n    fetch_path: headless_export\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "email_routing.surface_guard._M365_REGISTRY",
        registry,
    )
    result = check_mailbox_surface(mailbox="user@example.com")
    assert result is not None
    assert result["error"] == "wrong_surface"
    assert result["owning_surface"] == "m365_graph"
    assert "headless_export" in result["invoke_hint"]


def test_imap_folder_name_not_guarded(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    registry = tmp_path / "m365_accounts.yaml"
    registry.write_text("accounts: {}\n", encoding="utf-8")
    monkeypatch.setattr("email_routing.surface_guard._M365_REGISTRY", registry)
    assert check_mailbox_surface(mailbox="INBOX") is None
    assert check_mailbox_surface(mailbox="Sent") is None


def test_imap_upn_on_bridge_allowed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(
        "EMAIL_BRIDGE_ACCOUNTS_JSON",
        json.dumps([{"user": "work@he.net", "password": "x"}]),
    )
    assert check_mailbox_surface(mailbox="work@he.net") is None


def test_indeterminate_when_degraded_and_empty() -> None:
    base = {"total": 0, "emails": [], "query": {}}
    status = {"healthy": False, "degraded_reason": "autopull stale"}
    out = apply_indeterminate_if_degraded(base, status=status)
    assert out["status"] == "indeterminate"
    assert "autopull stale" in out["reason"]


def test_indeterminate_not_applied_when_results_present() -> None:
    base = {"total": 1, "emails": [{"message_id": "x"}]}
    status = {"healthy": False, "degraded_reason": "stale"}
    out = apply_indeterminate_if_degraded(base, status=status)
    assert "status" not in out or out.get("status") != "indeterminate"


def test_wrong_surface_response_shape() -> None:
    payload = wrong_surface_response(
        owning_surface="m365_graph", mailbox="a@b.com"
    )
    assert payload["error"] == "wrong_surface"
    assert "invoke_hint" in payload
