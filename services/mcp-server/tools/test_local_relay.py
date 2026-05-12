from __future__ import annotations

from tools._local_relay import _resolve_timeout


def test_email_pull_uses_extended_timeout() -> None:
    assert _resolve_timeout("email-bridge", "POST", "/pull") == 120.0


def test_local_relay_uses_default_timeout_for_unlisted_routes() -> None:
    assert _resolve_timeout("email-bridge", "GET", "/status") == 30.0
