"""NODE_OPTIONS proxyshim append tests."""

from __future__ import annotations

import pytest
from grokbot_box_mcp.headers import (
    PROXYSHIM_NODE_REQUIRE,
    append_proxyshim_node_options,
)


@pytest.mark.offline
@pytest.mark.parametrize(
    ("incoming", "expected_contains"),
    [
        (None, PROXYSHIM_NODE_REQUIRE),
        ("", PROXYSHIM_NODE_REQUIRE),
        ("--max-old-space-size=4096", PROXYSHIM_NODE_REQUIRE),
        (PROXYSHIM_NODE_REQUIRE, PROXYSHIM_NODE_REQUIRE),
    ],
)
def test_append_proxyshim_node_options(
    incoming: str | None, expected_contains: str
) -> None:
    out = append_proxyshim_node_options(incoming)
    assert PROXYSHIM_NODE_REQUIRE in out
    if incoming and incoming.strip() and incoming != PROXYSHIM_NODE_REQUIRE:
        assert incoming in out
