from __future__ import annotations

from tools._local_relay import resolve_timeout


def test_email_pull_uses_extended_timeout() -> None:
    assert resolve_timeout("email-bridge", "POST", "/pull") == 120.0


def test_local_relay_uses_default_timeout_for_unlisted_routes() -> None:
    assert resolve_timeout("email-bridge", "GET", "/status") == 30.0


def test_review_extract_parameterized_route_uses_long_timeout() -> None:
    assert resolve_timeout("email-bridge", "POST", "/review/<msg-id>/extract") == 200.0


def test_review_dismiss_stays_on_default_timeout() -> None:
    assert resolve_timeout("email-bridge", "POST", "/review/<msg-id>/dismiss") == 30.0
