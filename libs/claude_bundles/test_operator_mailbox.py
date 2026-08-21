"""Tests for operator-proxy mailbox predicate."""

from __future__ import annotations

from claude_bundles.operator_mailbox import is_operator_proxy_mailbox


def test_is_operator_proxy_mailbox_web_anthropic():
    assert is_operator_proxy_mailbox("web-anthropic") is True


def test_is_operator_proxy_mailbox_cdp_operator():
    assert is_operator_proxy_mailbox("cdp-operator-6655-day5i") is True


def test_is_operator_proxy_mailbox_cursor_false():
    assert is_operator_proxy_mailbox("cursor") is False
