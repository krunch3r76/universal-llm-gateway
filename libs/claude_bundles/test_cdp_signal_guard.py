"""The CDP stop path refuses process groups it did not launch."""

from __future__ import annotations

import os
import signal

import pytest

from claude_bundles import cdp_lane

pytestmark = pytest.mark.offline


def _pgid(mapping: dict[int, int]):
    def _getpgid(pid: int) -> int:
        if pid not in mapping:
            raise ProcessLookupError(pid)
        return mapping[pid]

    return _getpgid


def test_refuses_pid_one_with_no_signal(cdp_lane_signal_recorder: list) -> None:
    with pytest.raises(cdp_lane.SignalChildRefusedError):
        cdp_lane._default_signal_child(1)
    assert cdp_lane_signal_recorder == []


def test_refuses_pid_zero_with_no_signal(cdp_lane_signal_recorder: list) -> None:
    with pytest.raises(cdp_lane.SignalChildRefusedError):
        cdp_lane._default_signal_child(0)
    assert cdp_lane_signal_recorder == []


def test_refuses_pid_negative_with_no_signal(cdp_lane_signal_recorder: list) -> None:
    with pytest.raises(cdp_lane.SignalChildRefusedError):
        cdp_lane._default_signal_child(-1)
    assert cdp_lane_signal_recorder == []


def test_refuses_pgid_one_with_no_signal(
    monkeypatch: pytest.MonkeyPatch, cdp_lane_signal_recorder: list
) -> None:
    monkeypatch.setattr(os, "getpgid", _pgid({0: 50, 25000: 1}))
    with pytest.raises(cdp_lane.SignalChildRefusedError):
        cdp_lane._default_signal_child(25000)
    assert cdp_lane_signal_recorder == []


def test_refuses_caller_group_with_no_signal(
    monkeypatch: pytest.MonkeyPatch, cdp_lane_signal_recorder: list
) -> None:
    monkeypatch.setattr(os, "getpgid", _pgid({0: 50, 25000: 50}))
    with pytest.raises(cdp_lane.SignalChildRefusedError):
        cdp_lane._default_signal_child(25000)
    assert cdp_lane_signal_recorder == []


def test_signals_group_leader_term_then_kill(
    monkeypatch: pytest.MonkeyPatch, cdp_lane_signal_recorder: list
) -> None:
    monkeypatch.setattr(os, "getpgid", _pgid({0: 50, 25000: 25000}))
    cdp_lane._default_signal_child(25000)
    assert cdp_lane_signal_recorder == [
        ("killpg", 25000, signal.SIGTERM),
        ("killpg", 25000, signal.SIGKILL),
    ]


def test_kill_lane_chrome_uses_the_same_guard(
    monkeypatch: pytest.MonkeyPatch, cdp_lane_signal_recorder: list
) -> None:
    monkeypatch.setattr(os, "getpgid", _pgid({0: 50, 25000: 1}))
    cdp_lane._kill_lane_chrome(25000)
    assert cdp_lane_signal_recorder == []
