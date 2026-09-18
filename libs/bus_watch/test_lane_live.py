"""OLN live tri-state — process-start probe, SHA alone is never live."""

from __future__ import annotations

from bus_watch.lane_live import probe_live


class _Inspect:
    def __init__(
        self,
        *,
        started: float | None,
        committed: float | None,
        on_head: bool | None,
    ) -> None:
        self.started = started
        self.committed = committed
        self.on_head = on_head

    def ticker_start_unix(self, root: str) -> float | None:
        assert root == "11667"
        return self.started

    def commit_unix(self, sha: str) -> float | None:
        assert sha
        return self.committed

    def sha_on_head(self, sha: str) -> bool | None:
        assert sha
        return self.on_head


def test_probe_live_unprobed_with_landed_sha_only() -> None:
    assert probe_live("11692", "0e6653cdf") == "unprobed"


def test_probe_live_unprobed_when_ticker_missing() -> None:
    inspect = _Inspect(started=None, committed=100.0, on_head=True)
    assert (
        probe_live("11697", "02b419c62", parent_root="11667", inspect=inspect)
        == "unprobed"
    )


def test_probe_live_live_when_ticker_started_after_commit() -> None:
    inspect = _Inspect(started=200.0, committed=100.0, on_head=True)
    assert (
        probe_live("11697", "02b419c62", parent_root="11667", inspect=inspect)
        == "live"
    )


def test_probe_live_stale_when_ticker_started_before_commit() -> None:
    inspect = _Inspect(started=50.0, committed=100.0, on_head=True)
    assert (
        probe_live("11697", "02b419c62", parent_root="11667", inspect=inspect)
        == "stale"
    )


def test_probe_live_stale_when_sha_not_on_head() -> None:
    inspect = _Inspect(started=200.0, committed=100.0, on_head=False)
    assert (
        probe_live("11697", "deadbeef", parent_root="11667", inspect=inspect)
        == "stale"
    )
