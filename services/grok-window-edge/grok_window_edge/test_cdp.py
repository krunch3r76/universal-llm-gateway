"""The allowlist is an explicit set, and the hard denies are unconditional."""

from __future__ import annotations

import pytest

from grok_window_edge import cdp
from grok_window_edge.cdp import (
    CdpPortRefusedError,
    assert_cdp_origin_allowed,
    assert_display_allowed,
    assert_profile_dir_allowed,
    cdp_endpoint,
)
from grok_window_edge.config import CDP_PORT_ALLOWLIST, DENIED_PORTS


def test_a_9341_dial_raises() -> None:
    with pytest.raises(CdpPortRefusedError):
        cdp_endpoint(9341)


def test_non_loopback_http_origin_refused() -> None:
    with pytest.raises(CdpPortRefusedError, match="not loopback"):
        assert_cdp_origin_allowed("http://192.168.0.3:9339")


@pytest.mark.parametrize("port", [9339, 9340])
def test_allowlisted_ports_build_a_loopback_endpoint(port: int) -> None:
    assert cdp_endpoint(port) == f"http://127.0.0.1:{port}"


def test_the_allowlist_is_the_two_grok_ports() -> None:
    assert CDP_PORT_ALLOWLIST == frozenset({9339, 9340})


@pytest.mark.parametrize("port", [9338, 9341, 19338, 0, -1, 65535])
def test_every_other_port_is_refused(port: int) -> None:
    with pytest.raises(CdpPortRefusedError):
        cdp_endpoint(port)


def test_the_neighbours_of_the_allowed_ports_are_not_implied() -> None:
    """A set, not a range: 9341 is hard-denied beside the allowlist."""
    with pytest.raises(CdpPortRefusedError):
        cdp_endpoint(9341)


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


def test_a_hard_denied_port_is_refused_even_when_allowlisted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The deny is read before the allowlist, so widening it cannot reach 9341.

    Binding 8's flips-if contemplates 9341 being released later, which means
    the allowlist will be edited. The denies survive that edit only because
    they are consulted first; left merely off the allowlist they would not.
    """
    monkeypatch.setattr(cdp, "CDP_PORT_ALLOWLIST", frozenset({9339, 9340, 9341, 9314}))
    for port in sorted(DENIED_PORTS):
        with pytest.raises(CdpPortRefusedError, match="hard-denied"):
            cdp.cdp_endpoint(port)
    assert cdp.cdp_endpoint(9339) == "http://127.0.0.1:9339"


def test_the_denied_ports_are_the_cookie_sustain_pair_and_disjoint() -> None:
    """Binding 9's hard-deny list, and the allowlist it must never overlap."""
    assert DENIED_PORTS == frozenset({9314, 9341})
    assert CDP_PORT_ALLOWLIST == frozenset({9339, 9340})
    assert not DENIED_PORTS & CDP_PORT_ALLOWLIST
