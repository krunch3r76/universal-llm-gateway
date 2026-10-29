"""Offline tests for Orion synthetic wake route and echo drop."""

from __future__ import annotations

import pytest

from bus_watch.endpoint_wake_dispatcher import _orion_synthetic_route, qualify

pytestmark = pytest.mark.offline


def test_orion_synthetic_route_house_only() -> None:
    bound = _orion_synthetic_route("orion", "21207")
    assert bound is not None
    assert bound[1]["surface"] == "chatgpt"
    assert _orion_synthetic_route("orion", "99999") is None
    assert _orion_synthetic_route("cursor", "21207") is None


def test_qualify_drops_orion_echo() -> None:
    turn = {
        "to_agent": "orion",
        "from_agent": "orion",
        "subject": "ANSWER|self",
        "thread": "21207",
        "id": 1,
    }
    verdict = qualify(turn, cse=False, store=None)
    assert verdict.wake is False
    assert verdict.rule_id in {"drop_self_or_broadcast", "drop_orion_echo"}
