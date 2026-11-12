"""Truth table for ``is_cursor_xvfb_host`` (neutral host_predicates package)."""

from __future__ import annotations

import pytest

from host_predicates.cursor_hosts import is_cursor_xvfb_host

pytestmark = pytest.mark.offline


@pytest.mark.parametrize(
    "host",
    [
        ":8",
        "8",
        "io:8",
        "127.0.0.1:9338",
        "http://127.0.0.1:9338",
        "orion-node",
        "http://127.0.0.1:19338",
    ],
)
def test_is_cursor_xvfb_host_configured_aliases(host: str) -> None:
    assert is_cursor_xvfb_host(host) is True


def test_is_cursor_xvfb_host_jupiter_negative() -> None:
    assert is_cursor_xvfb_host("jupiter") is False


@pytest.mark.parametrize("host", [None, ""])
def test_is_cursor_xvfb_host_empty_defaults_to_glass_row(host: str | None) -> None:
    """None and empty string resolve to the default ``:8`` table row."""
    assert is_cursor_xvfb_host(host) is True
