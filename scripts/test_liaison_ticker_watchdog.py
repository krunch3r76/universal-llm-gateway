"""Tests for the liaison ticker watchdog.

The point of the watchdog is that it fires when nothing else will, so these
tests pin the classification boundary AND the fact that a stale clock actually
reaches the pager. A watchdog that classifies correctly but never pages is the
same failure as no watchdog at all.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from unittest import mock

import liaison_ticker_watchdog as wd
import pytest

_NOW = datetime(2026, 9, 14, 12, 0, 0, tzinfo=UTC)


def _state(tmp_path: Path, last_tick_at: str | None, ticks: int = 1619) -> Path:
    payload: dict[str, object] = {"ticks": ticks}
    if last_tick_at is not None:
        payload["last_tick_at"] = last_tick_at
    path = tmp_path / "liaison-10479.tick.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


@pytest.mark.offline
def test_fresh_tick_is_healthy(tmp_path: Path) -> None:
    status = wd.evaluate(
        _state(tmp_path, "2026-09-14T11:58:00Z"), stale_after_s=600.0, now=_NOW
    )
    assert status.healthy is True
    assert status.age_s == pytest.approx(120.0)


@pytest.mark.offline
def test_stale_tick_is_unhealthy(tmp_path: Path) -> None:
    status = wd.evaluate(
        _state(tmp_path, "2026-09-14T11:40:00Z"), stale_after_s=600.0, now=_NOW
    )
    assert status.healthy is False
    assert status.reason == "ticker_stale"
    assert "1200s ago" in status.detail


@pytest.mark.offline
def test_boundary_is_not_paged_early(tmp_path: Path) -> None:
    """Exactly at the limit is still healthy — fire on exceeded, not reached."""
    status = wd.evaluate(
        _state(tmp_path, "2026-09-14T11:50:00Z"), stale_after_s=600.0, now=_NOW
    )
    assert status.healthy is True


@pytest.mark.offline
def test_missing_state_is_unhealthy(tmp_path: Path) -> None:
    """Absent state is a fault, not a pass: the ticker cannot report its own death."""
    status = wd.evaluate(tmp_path / "nope.json", stale_after_s=600.0, now=_NOW)
    assert status.healthy is False
    assert status.reason == "state_missing"


@pytest.mark.offline
def test_unparseable_last_tick_is_unhealthy(tmp_path: Path) -> None:
    status = wd.evaluate(
        _state(tmp_path, "not-a-timestamp"), stale_after_s=600.0, now=_NOW
    )
    assert status.healthy is False
    assert status.reason == "no_last_tick"


@pytest.mark.offline
def test_stale_clock_actually_reaches_the_pager(tmp_path: Path) -> None:
    """The load-bearing test: staleness must reach notify_tick_sos.

    Classification alone proves nothing — this session already shipped a feature
    whose gate silently returned False everywhere, green suite and all.
    """
    sent: dict[str, object] = {}

    async def _fake_notify(**kwargs: object) -> bool:
        sent.update(kwargs)
        return True

    with (
        mock.patch.object(wd, "claim_tick_sos", return_value=True) as claim,
        mock.patch.object(wd, "notify_tick_sos", _fake_notify),
    ):
        rc = wd.main(
            [
                "--root",
                "10479",
                "--state-file",
                str(_state(tmp_path, "2020-01-01T00:00:00Z")),
            ]
        )

    assert rc == 0
    assert claim.called, "stale clock must claim an SOS"
    assert sent["root_id"] == "10479"
    assert "CLOCK DOWN" in str(sent["reason"])


@pytest.mark.offline
def test_healthy_clock_does_not_page(tmp_path: Path) -> None:
    now_iso = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    with mock.patch.object(wd, "claim_tick_sos") as claim:
        rc = wd.main(["--state-file", str(_state(tmp_path, now_iso))])
    assert rc == 0
    assert not claim.called


@pytest.mark.offline
def test_dedupe_suppression_is_not_an_error(tmp_path: Path) -> None:
    """A suppressed re-page is a normal outcome, not a failed watchdog run."""
    with mock.patch.object(wd, "claim_tick_sos", return_value=False):
        rc = wd.main(["--state-file", str(_state(tmp_path, "2020-01-01T00:00:00Z"))])
    assert rc == 0
