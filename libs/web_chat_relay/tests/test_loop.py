"""Hermetic fingerprint / loop-break / baseline tests for the relay."""

from __future__ import annotations

import pytest
from web_chat_relay.loop import body_sha, should_relay

pytestmark = pytest.mark.offline


def test_body_sha_stable() -> None:
    assert body_sha("hello") == body_sha("hello")
    assert body_sha("hello") != body_sha("hello ")


def test_should_relay_skips_baseline() -> None:
    sha = body_sha("opener reply")
    assert not should_relay(
        new_sha=sha,
        baseline_sha=sha,
        last_sent_sha=None,
        last_received_sha=None,
    )


def test_should_relay_skips_echo_of_just_pasted() -> None:
    incoming = body_sha("from grok")
    assert not should_relay(
        new_sha=incoming,
        baseline_sha=body_sha("old"),
        last_sent_sha=incoming,
        last_received_sha=None,
    )
    assert not should_relay(
        new_sha=incoming,
        baseline_sha=body_sha("old"),
        last_sent_sha=None,
        last_received_sha=incoming,
    )


def test_should_relay_accepts_new_turn() -> None:
    assert should_relay(
        new_sha=body_sha("new assistant"),
        baseline_sha=body_sha("history"),
        last_sent_sha=body_sha("prior paste"),
        last_received_sha=body_sha("prior harvest"),
    )


def test_should_relay_rejects_empty() -> None:
    empty = body_sha("")
    assert not should_relay(
        new_sha=empty,
        baseline_sha=body_sha("x"),
        last_sent_sha=None,
        last_received_sha=None,
    )
