"""A bridge that outlives its terminal dispatch must not hold the worktree.

Regression cover for a:33686. lane-11231 was fully landed (its tip an ancestor
of master, zero unique commits) yet ``discharge_landed`` refused with
``live_bridge`` indefinitely, because pid 223045 stayed alive ~50 minutes past
its own dispatch closeout at 0% CPU and the guard keyed on process liveness
alone.

The asymmetry these tests pin is the safety-critical part: dropping the claim
is correct ONLY when the dispatch is known and terminal. An absent row or an
unreadable ledger must still claim, because "I could not tell" must never reap
a worktree out from under running work.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest import mock

import pytest

from services.git_integration_worker import cursor_sdk_worktree_live_guard as guard
from services.git_integration_worker.cursor_sdk_orphan import BridgeOccupancy

_DISPATCH = "58b654ca6b5c-23f04777"
_LEASE = "/mnt/torus/projects/ulg-arc-worktrees/universal-llm-gateway/lane-11231"


def _row(status: str, *, terminal_age_s: float | None = 3600.0) -> sqlite3.Row:
    """A ledger row whose dispatch went terminal ``terminal_age_s`` ago."""
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


def _paths_for(row: sqlite3.Row | None, *, raises: bool = False) -> set[str]:
    import contextlib

    @contextlib.contextmanager
    def _ledger():  # noqa: ANN202
        if raises:
            raise sqlite3.OperationalError("ledger unreadable")
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


@pytest.mark.offline
@pytest.mark.parametrize("status", ["completed", "failed", "cancelled"])
def test_terminal_dispatch_releases_its_claim(status: str) -> None:
    assert _paths_for(_row(status)) == set(), (
        f"a {status} dispatch must not hold a worktree"
    )


@pytest.mark.offline
@pytest.mark.parametrize(
    "status", ["admitted", "running", "queued", "parked_waiting"]
)
def test_live_dispatch_keeps_its_claim(status: str) -> None:
    """The safety half: in-flight work must never be unguarded."""
    assert _LEASE in _paths_for(_row(status)), (
        f"a {status} dispatch MUST keep its worktree claim"
    )


@pytest.mark.offline
def test_absent_row_is_not_treated_as_terminal() -> None:
    """No row means 'I cannot tell', which must not log or take the drop path."""
    with mock.patch.object(guard, "logger") as log:
        _paths_for(None)
    assert not log.warning.called, "an absent row must not be reported as an orphan"


@pytest.mark.offline
def test_unreadable_ledger_does_not_unguard() -> None:
    """A failed read must fall through, never silently release the tree."""
    assert _paths_for(None, raises=True) == set()


@pytest.mark.offline
@pytest.mark.parametrize("status", ["completed", "failed", "cancelled"])
def test_terminal_but_still_flushing_keeps_its_claim(status: str) -> None:
    """The race this module exists to prevent.

    A bridge marked terminal one second ago may still be flushing. Reaping it
    costs the write, so the claim survives the grace window — the behaviour
    ``test_sweep_leaves_tree_held_by_env_stamped_bridge`` pins.
    """
    assert _LEASE in _paths_for(_row(status, terminal_age_s=1.0)), (
        "a just-terminal bridge must keep its claim while it flushes"
    )


@pytest.mark.offline
def test_missing_terminal_at_keeps_its_claim() -> None:
    """Cannot prove the grace elapsed ⇒ fail closed."""
    assert _LEASE in _paths_for(_row("completed", terminal_age_s=None))


@pytest.mark.offline
def test_unparseable_terminal_at_keeps_its_claim() -> None:
    row = _row("completed")
    broken = dict(zip(row.keys(), tuple(row), strict=True)) | {
        "terminal_at": "not-a-timestamp"
    }
    assert not guard._terminal_claim_expired(broken)  # type: ignore[arg-type]


@pytest.mark.offline
def test_grace_boundary_is_the_documented_window() -> None:
    """Just inside holds, just outside releases — the discriminator itself."""
    inside = _row("completed", terminal_age_s=guard._TERMINAL_CLAIM_GRACE_S - 30)
    outside = _row("completed", terminal_age_s=guard._TERMINAL_CLAIM_GRACE_S + 30)
    assert not guard._terminal_claim_expired(inside)
    assert guard._terminal_claim_expired(outside)


@pytest.mark.offline
def test_live_status_set_matches_the_ledger_filter() -> None:
    """Both guard paths must agree on what 'live' means.

    ``live_ledger_worktree_paths`` already filtered on this set; the process
    path did not, which is the entire defect. If they ever diverge, one path
    would strand branches while the other reaps live work.
    """
    assert guard._LIVE_LEDGER_STATUSES == (
        "admitted",
        "running",
        "queued",
        "parked_waiting",
    )


@pytest.mark.offline
def test_terminal_cwd_branch_releases_past_grace(tmp_path: Path) -> None:
    """Falsifier for a:33686 — cwd-derived claim must expire like dispatch-path.

    When the ledger row is terminal and past ``_TERMINAL_CLAIM_GRACE_S``, the
    dispatch-path branch drops its claim. The cwd branch does not today, which
    strands branches indefinitely (lane-11231). Mock ``_dispatch_worktree_paths``
    empty so only the cwd signal is under test.
    """
    worktree_root = tmp_path / "worktrees"
    lane_tree = worktree_root / "universal-llm-gateway" / "lane-11243"
    lane_tree.mkdir(parents=True)

    occ = [BridgeOccupancy(pid=1, cwd=str(lane_tree), dispatch_id=_DISPATCH)]

    with mock.patch.object(guard, "_dispatch_worktree_paths", return_value=set()):
        result = guard.worktree_held_by_live_bridge(
            worktree_path=lane_tree,
            worktree_root=worktree_root,
            occupancy=occ,
        )

    assert result is None, (
        "a terminal dispatch past grace must not hold via cwd alone"
    )
