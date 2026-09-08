"""Tests for observability target resolution (ulg vs claudeburst)."""

from __future__ import annotations

from tools.events import _resolve_target


def test_resolve_target_ulg_default() -> None:
    target = _resolve_target("")
    assert target is not None
    assert target.name == "ulg"
    assert target.url.endswith("events-query.sock")


def test_resolve_target_claudeburst() -> None:
    target = _resolve_target("claudeburst")
    assert target is not None
    assert target.name == "claudeburst"
    assert target.url.endswith("claudeburst-events-query.sock")


def test_resolve_target_unknown() -> None:
    assert _resolve_target("satellite-foo") is None
