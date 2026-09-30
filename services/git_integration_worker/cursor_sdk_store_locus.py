"""SDK store locus: which dispatch HOME owns the sqlite agent store.

The cursor SDK keys agents by HOME plus cwd (store-A), so a ledger
``state_root`` names a store directory but never the HOME that owns it.
``record_resolved_store_roots`` copies that path onto every resumed row.
Callers are the resume plane, park preflight, and ``routes/cursor_sdk``.
Ownership is the lineage HOME that contains the resolved store path.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

from services.git_integration_worker import cursor_home
from services.git_integration_worker.cursor_dispatch_ledger import (
    CursorDispatchLedger,
    LedgerRow,
)
from services.git_integration_worker.cursor_sdk_resume_store_events import (
    emit_resume_store_owner_resolved,
)

MAX_RESUME_LINEAGE_HOPS = 11


def load_row_columns(
    ledger: CursorDispatchLedger, *, dispatch_id: str, columns: str
) -> dict[str, Any] | None:
    """Read selected ledger columns for one dispatch, or None when the row is absent.

    ``columns`` is interpolated into SQL and must be a caller-owned projection,
    never request input. Lineage walks and resume retain checks share this read.
    """
    with ledger._connect() as conn:
        row = conn.execute(
            f"SELECT {columns} FROM cursor_sdk_dispatches WHERE dispatch_id=?",
            (dispatch_id,),
        ).fetchone()
    if row is None:
        return None
    return {k: row[k] for k in row.keys()}


def load_parent_row(
    ledger: CursorDispatchLedger, *, parent_id: str
) -> LedgerRow | None:
    """Load the parent ledger projection used by resume eligibility and child binding.

    Returns None when ``parent_id`` has no row. Does not follow ``resume_of``;
    lineage walks call this once per hop.
    """
    data = load_row_columns(
        ledger,
        dispatch_id=parent_id,
        columns=(
            "dispatch_id, thread_id, execution_id, caller_agent, resolved_model, "
            "state_root, sdk_agent_id, sdk_run_id, status, started_at, "
            "last_heartbeat_at, source_repo, contract, read_only, record_json, "
            "terminal_status"
        ),
    )
    if data is None:
        return None
    return LedgerRow(**data)


def _find_sdk_store_under_home(home: Path) -> Path | None:
    """Return the sdk-agent-store directory under a dispatch HOME, if present."""
    projects = home / ".cursor" / "projects"
    if not projects.is_dir():
        return None
    for candidate in projects.rglob("sdk-agent-store"):
        if candidate.is_dir():
            return candidate
    return None


def _state_root_store(state_root: str | None) -> Path | None:
    """Return the store a non-empty ``state_root`` names, else None.

    A miss (missing, not a directory, or empty without an ``sdk-agent-store``
    child) means the hop must rescan that dispatch HOME.
    """
    if not state_root:
        return None
    root_path = Path(state_root)
    if not root_path.is_dir():
        return None
    if any(root_path.iterdir()):
        return root_path
    store_in_root = root_path / "sdk-agent-store"
    if store_in_root.is_dir():
        return store_in_root
    return None


def _store_at_dispatch(*, dispatch_id: str, state_root: str | None) -> Path | None:
    """Return SDK store path for one dispatch row, or None."""
    named = _state_root_store(state_root)
    if named is not None:
        return named
    dispatch_home = cursor_home.dispatch_home_path(dispatch_id)
    return _find_sdk_store_under_home(dispatch_home)


def _iter_resume_lineage(
    ledger: CursorDispatchLedger, *, start_id: str
) -> Iterator[tuple[str, str | None]]:
    """Yield ``(dispatch_id, state_root)`` walking ``resume_of`` toward ancestors."""
    current = start_id
    seen: set[str] = set()
    for _ in range(MAX_RESUME_LINEAGE_HOPS):
        if current in seen:
            break
        seen.add(current)
        row = load_parent_row(ledger, parent_id=current)
        if row is None:
            break
        yield current, row.state_root
        link = load_row_columns(ledger, dispatch_id=current, columns="resume_of")
        if link is None or not link.get("resume_of"):
            break
        current = str(link["resume_of"])


def _hit_for_dispatch(
    *, dispatch_id: str, state_root: str | None
) -> tuple[Path | None, bool]:
    """Return ``(store, rescanned)`` for one lineage hop.

    ``rescanned`` is true only when the hit came from
    ``_find_sdk_store_under_home`` because ``state_root`` did not name a store.
    """
    named = _state_root_store(state_root)
    if named is not None:
        return named, False
    found = _store_at_dispatch(dispatch_id=dispatch_id, state_root=None)
    if found is not None:
        return found, True
    return None, False


def _locate_store_for_owner(
    *, parent_id: str, state_root: str | None
) -> tuple[Path | None, bool]:
    """Walk resume lineage the same way ``resolve_sdk_store_dir`` does.

    Returns ``(path, rescanned)``. ``rescanned`` is true only when the winning
    hit is the HOME scan (``state_root`` stale, missing, empty, or not a
    directory). A missing store is ``(None, False)``.
    """
    ledger = CursorDispatchLedger.instance()
    if load_parent_row(ledger, parent_id=parent_id) is None:
        return _hit_for_dispatch(dispatch_id=parent_id, state_root=state_root)
    first = True
    for dispatch_id, row_state_root in _iter_resume_lineage(ledger, start_id=parent_id):
        sr = state_root if first else row_state_root
        first = False
        found, rescanned = _hit_for_dispatch(dispatch_id=dispatch_id, state_root=sr)
        if found is not None:
            return found, rescanned
    return None, False


def resolve_sdk_store_dir(
    *,
    parent_id: str,
    state_root: str | None,
) -> Path | None:
    """Locate the on-disk SDK sqlite store for a resume parent.

    Prefers a non-empty ``state_root`` directory; falls back to the store under
    each dispatch HOME while walking ``resume_of`` lineage (store-A — multi-hop
    resume_of may leave intermediate rows with empty bridge-state). Discards
    the rescan flag ``_locate_store_for_owner`` uses for the owner event.
    """
    found, _rescanned = _locate_store_for_owner(
        parent_id=parent_id, state_root=state_root
    )
    return found


def _owning_dispatch_for_store(
    ledger: CursorDispatchLedger, *, start_id: str, store_dir: Path
) -> str | None:
    """Return the lineage dispatch whose own HOME contains *store_dir*.

    Precondition: HOMEs are disjoint and non-nested, so at most one matches.
    If that precondition is violated, the deepest containing HOME wins
    (longest resolved path).
    """
    deepest_id: str | None = None
    deepest_len = -1
    for dispatch_id, _ in _iter_resume_lineage(ledger, start_id=start_id):
        try:
            home = cursor_home.dispatch_home_path(dispatch_id).resolve()
        except OSError:
            continue
        if store_dir.is_relative_to(home) and len(home.parts) > deepest_len:
            deepest_id = dispatch_id
            deepest_len = len(home.parts)
    return deepest_id


def resolve_store_bearing_dispatch_id(*, parent_id: str) -> str:
    """Return the lineage dispatch whose own HOME owns the parent's SDK store.

    ``record_resolved_store_roots`` copies the resolved store path onto every
    resumed row, so ``state_root`` names the store but never the HOME that owns
    it. The SDK keys agents by HOME plus cwd (store-A), so ownership is derived
    from the store path rather than from which row carries a non-empty
    ``state_root``.

    Emits ``giw.resume.store.owner.resolved`` when a store path is located.
    A missing store returns ``parent_id`` and does not emit. A store outside
    every lineage HOME also returns ``parent_id`` with ``mode=external``.
    A store under the parent HOME when the lineage walk returns no owner is
    ``rescanned`` or ``home_contained``; ``external`` remains the
    outside-every-HOME case.
    """
    ledger = CursorDispatchLedger.instance()
    parent = load_parent_row(ledger, parent_id=parent_id)
    store_dir, rescanned = _locate_store_for_owner(
        parent_id=parent_id,
        state_root=parent.state_root if parent else None,
    )
    if store_dir is None:
        return parent_id
    owner = _owning_dispatch_for_store(
        ledger, start_id=parent_id, store_dir=store_dir.resolve()
    )
    if owner is None:
        contained = False
        try:
            home = cursor_home.dispatch_home_path(parent_id).resolve()
            contained = store_dir.resolve().is_relative_to(home)
        except OSError:
            contained = False
        if not contained:
            mode = "external"
        elif rescanned:
            mode = "rescanned"
        else:
            mode = "home_contained"
        owner_id = parent_id
    elif rescanned:
        mode = "rescanned"
        owner_id = owner
    else:
        mode = "home_contained"
        owner_id = owner
    emit_resume_store_owner_resolved(
        parent_id=parent_id,
        owner_dispatch_id=owner_id,
        store_path=str(store_dir),
        mode=mode,
    )
    return owner_id


def record_resolved_store_roots(
    *,
    parent_id: str,
    child_id: str,
    parent_state_root: str | None = None,
) -> str | None:
    """Persist the real SDK store path on parent and child ledger rows.

    Replaces a lying ``state_root`` (empty ``bridge-state``) with the path
    ``resolve_sdk_store_dir`` finds — typically the HOME-bound
    ``sdk-agent-store`` (store-A). Writes both rows; returns None when no
    store is on disk.
    """
    store_dir = resolve_sdk_store_dir(
        parent_id=parent_id,
        state_root=parent_state_root,
    )
    if store_dir is None:
        return None
    store_path = str(store_dir)
    ledger = CursorDispatchLedger.instance()
    ledger.record_state_root(dispatch_id=parent_id, state_root=store_path)
    ledger.record_state_root(dispatch_id=child_id, state_root=store_path)
    return store_path
