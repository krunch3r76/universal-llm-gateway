"""The allowlist is an explicit set, and the hard denies are unconditional."""

from __future__ import annotations

import pytest

from grok_window_edge.cdp import (
    CdpPortRefusedError,
    assert_display_allowed,
    assert_profile_dir_allowed,
    cdp_endpoint,
)
from grok_window_edge.config import CDP_PORT_ALLOWLIST


def test_a_9341_dial_raises() -> None:
    with pytest.raises(CdpPortRefusedError):
        cdp_endpoint(9341)


@pytest.mark.parametrize("port", [9339, 9340])
def test_the_grok_ports_build_a_loopback_endpoint(port: int) -> None:
    assert cdp_endpoint(port) == f"http://127.0.0.1:{port}"


def test_the_allowlist_is_exactly_the_two_grok_ports() -> None:
    assert CDP_PORT_ALLOWLIST == frozenset({9339, 9340})


@pytest.mark.parametrize("port", [9338, 9341, 19338, 0, -1, 65535])
def test_every_other_port_is_refused(port: int) -> None:
    with pytest.raises(CdpPortRefusedError):
        cdp_endpoint(port)


def test_the_neighbours_of_the_allowed_ports_are_not_implied() -> None:
    """A set, not a range: 9338 and 9341 bracket the allowlist and both fail."""
    for port in (9338, 9341):
        with pytest.raises(CdpPortRefusedError):
            cdp_endpoint(port)


def test_the_new_display_fourteen_port_is_refused() -> None:
    """CDP 9314 (display :14) appeared after A1 and is on no allowlist.

    It is refused by the same deny-by-default rule as 9341, and this service
    does not add it: the allowlist stays the two grok ports until a review
    says otherwise.
    """
    for port in (9314, 9341):
        with pytest.raises(CdpPortRefusedError):
            cdp_endpoint(port)
    assert 9314 not in CDP_PORT_ALLOWLIST


def test_display_eleven_is_hard_denied() -> None:
    with pytest.raises(CdpPortRefusedError):
        assert_display_allowed(":11")
    assert_display_allowed(":9")


@pytest.mark.parametrize(
    "path",
    ["~/.gateway/chrome-playground", "~/.gateway/chrome-playground/Default"],
)
def test_the_playground_profile_is_hard_denied(path: str) -> None:
    with pytest.raises(CdpPortRefusedError):
        assert_profile_dir_allowed(path)


def test_an_unrelated_profile_dir_is_allowed() -> None:
    assert_profile_dir_allowed("~/.gateway/grok-window")
