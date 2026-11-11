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
def test_is_cursor_xvfb_host(host: str) -> None:
    assert is_cursor_xvfb_host(host) is True
    assert is_cursor_xvfb_host("jupiter") is False
