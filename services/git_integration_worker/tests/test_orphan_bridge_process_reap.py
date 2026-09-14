"""Process reap for dispatch-scoped bridges past terminal grace (a:33741).

``test_orphan_bridge_claim`` pins claim release; this module pins that the OS
process is reaped (or explicitly killed via ``reap_orphan_bridge_os``) once
``_TERMINAL_CLAIM_GRACE_S`` has elapsed — not merely that the worktree claim
drops while pid 223045 survives for hours at 0% CPU.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta
from unittest import mock

import pytest

from services.git_integration_worker import cursor_sdk_orphan as orphan_mod
from services.git_integration_worker import cursor_sdk_worktree_live_guard as guard
from services.git_integration_worker.cursor_sdk_orphan import (
    BridgeOccupancy,
    BridgeReapResult,
)

_DISPATCH = "58b654ca6b5c-23f04777"
_LEASE = "/mnt/torus/projects/ulg-arc-worktrees/universal-llm-gateway/lane-11231"


def _row(status: str, *, terminal_age_s: float | None = 3600.0) -> sqlite3.Row:
    terminal_at = (
        None
        if terminal_age_s is None
        else (datetime.now(UTC) - timedelta(seconds=terminal_age_s)).isoformat()
    )
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        "CREATE TABLE d (lease_key TEXT, source_repo TEXT, status TEXT, "
        "terminal_at TEXT)"
    )
    conn.execute("INSERT INTO d VALUES (?, NULL, ?, ?)", (_LEASE, status, terminal_at))
    return conn.execute("SELECT * FROM d").fetchone()


class _Conn:
    def __init__(self, row: sqlite3.Row | None):
        self._row = row

    def execute(self, *_a: object, **_k: object) -> _Conn:
        return self

    def fetchone(self) -> sqlite3.Row | None:
        return self._row


def _paths_for(row: sqlite3.Row | None) -> set[str]:
    import contextlib

    @contextlib.contextmanager
    def _ledger():  # noqa: ANN202
        yield _Conn(row)

    with (
        mock.patch.object(guard, "ledger_connection", _ledger),
        mock.patch(
            "services.git_integration_worker.cursor_sdk_worktree_registry"
            ".lookup_dispatch_worktree",
            return_value=None,
        ),
    ):
        return guard._dispatch_worktree_paths(_DISPATCH)


class _FakeProc:
    """psutil.Process stand-in for OS-level bridge reap."""

    def __init__(
        self,
        pid: int,
        *,
        dispatch_id: str,
        is_bridge: bool = True,
    ) -> None:
        self.pid = pid
        self.is_bridge = is_bridge
        self.killed = False
        self._env = {"CURSOR_SDK_DISPATCH_ID": dispatch_id}

    def environ(self) -> dict[str, str]:
        return self._env

    def kill(self) -> None:
        self.killed = True

    def wait(self, timeout: float | None = None) -> int:
        del timeout
        return 0


@pytest.mark.offline
@pytest.mark.parametrize("status", ["completed", "failed", "cancelled"])
def test_terminal_past_grace_triggers_os_reap(status: str) -> None:
    """Claim release must reap the bridge process, not leave it alive (a:33741)."""
    row = _row(status, terminal_age_s=guard._TERMINAL_CLAIM_GRACE_S + 60)
    reaped: list[str] = []

    def _reap(dispatch_id: str) -> BridgeReapResult:
        reaped.append(dispatch_id)
        return BridgeReapResult(bridge_aborted=True)

    with mock.patch.object(orphan_mod, "reap_orphan_bridge_os", side_effect=_reap):
        assert _paths_for(row) == set()
    assert reaped == [_DISPATCH], (
        "past grace the guard must invoke OS reap for the stale bridge"
    )


@pytest.mark.offline
@pytest.mark.parametrize("status", ["completed", "failed", "cancelled"])
def test_terminal_within_grace_does_not_reap(status: str) -> None:
    """Flush window: claim kept and process must not be killed."""
    row = _row(status, terminal_age_s=guard._TERMINAL_CLAIM_GRACE_S - 30)
    with mock.patch.object(orphan_mod, "reap_orphan_bridge_os") as reap:
        assert _LEASE in _paths_for(row)
    reap.assert_not_called()


@pytest.mark.offline
def test_sweep_stale_terminal_bridges_kills_past_grace(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Periodic sweep reaps without waiting for ``_SWEEP_MIN_AGE_S`` (a:33741)."""
    proc = _FakeProc(223045, dispatch_id=_DISPATCH)
    monkeypatch.setattr(
        orphan_mod,
        "live_bridge_occupancy",
        lambda *, fresh=False: [
            BridgeOccupancy(pid=223045, cwd=_LEASE, dispatch_id=_DISPATCH)
        ],
    )
    monkeypatch.setattr(guard, "_dispatch_claim_stale", lambda did: did == _DISPATCH)
    monkeypatch.setattr(orphan_mod.psutil, "process_iter", lambda attrs=None: [proc])
    monkeypatch.setattr(
        orphan_mod, "is_cursor_sdk_bridge_process", lambda p: p.is_bridge
    )

    result = orphan_mod.sweep_stale_terminal_bridges()

    assert proc.killed is True
    assert result.killed == [223045]
    assert result.scanned == 1


@pytest.mark.offline
def test_sweep_stale_terminal_bridges_spares_within_grace(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    proc = _FakeProc(99, dispatch_id=_DISPATCH)
    monkeypatch.setattr(
        orphan_mod,
        "live_bridge_occupancy",
        lambda *, fresh=False: [
            BridgeOccupancy(pid=99, cwd=_LEASE, dispatch_id=_DISPATCH)
        ],
    )
    monkeypatch.setattr(guard, "_dispatch_claim_stale", lambda _did: False)
    monkeypatch.setattr(orphan_mod.psutil, "process_iter", lambda attrs=None: [proc])
    monkeypatch.setattr(
        orphan_mod, "is_cursor_sdk_bridge_process", lambda p: p.is_bridge
    )

    result = orphan_mod.sweep_stale_terminal_bridges()

    assert proc.killed is False
    assert result.killed == []
    assert result.scanned == 0


@pytest.mark.offline
def test_sweep_stale_terminal_bridges_spares_live_dispatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    live_id = "live-dispatch-id"
    proc = _FakeProc(100, dispatch_id=live_id)
    monkeypatch.setattr(
        orphan_mod,
        "live_bridge_occupancy",
        lambda *, fresh=False: [
            BridgeOccupancy(pid=100, cwd=_LEASE, dispatch_id=live_id)
        ],
    )
    monkeypatch.setattr(guard, "_dispatch_claim_stale", lambda _did: False)
    monkeypatch.setattr(orphan_mod.psutil, "process_iter", lambda attrs=None: [proc])

    result = orphan_mod.sweep_stale_terminal_bridges()

    assert proc.killed is False
    assert result.killed == []
