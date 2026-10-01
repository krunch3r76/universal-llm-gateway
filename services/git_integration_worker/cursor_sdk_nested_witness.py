"""Nested implement commit witness for conductor G5 (SF1)."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

# ``none`` authors lane commits on conductor resumes (cursor-auto).
# ``mechanical`` is a nested child contract that can author lane commits.
# ``freeform`` is the nested implement contract when implement admission refuses.
# The commits check below is the witness; the contract label is not.
# hub fold without mechanical in the set ignores a terminal mechanical child even when commits_ahead is 1.
_IMPLEMENT_JOBS = frozenset(
    {"implement", "pure-mechanical", "mechanical", "none", "freeform"}
)
_MAX_NEST_WALK = 12
_TERMINAL_STATUSES = frozenset({"completed", "failed", "cancelled"})
_COMMITS_AHEAD_RE = re.compile(r'(?i)(?:^|[,{])\s*"commits_ahead"\s*:\s*(\d+)')
_SIDECAR_REL = "tmp/reviews/closeouts/{dispatch_id}.md"
_DISPATCH_ON_THREAD_RE = re.compile(
    r"dispatch\s+`([0-9a-f-]{8,})`\s+on thread\s+(\d+)",
    re.IGNORECASE,
)


def _parse_record_json(raw: str | None) -> dict[str, Any]:
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def _commits_ahead_from_text(text: str) -> int | None:
    if not text:
        return None
    match = _COMMITS_AHEAD_RE.search(text)
    if match is None:
        return None
    return int(match.group(1))


def _nested_child_has_commits(
    *,
    dispatch_id: str,
    contract: str | None,
    status: str | None,
    record_json: str | None,
    wt_baseline: str | None,
    source_repo: str | None,
    worktree_path: str | None,
) -> bool:
    if str(contract or "") not in _IMPLEMENT_JOBS:
        return False
    if str(status or "") not in _TERMINAL_STATUSES:
        return False
    rec = _parse_record_json(record_json)
    closeout_body = str(rec.get("closeout_body") or "")
    for blob in (closeout_body, record_json or ""):
        ahead = _commits_ahead_from_text(blob)
        if ahead is not None and ahead > 0:
            return True
    baseline = _parse_record_json(wt_baseline)
    admit_head = baseline.get("admit_head")
    if not isinstance(admit_head, str) or not admit_head.strip():
        return False
    repo_candidates: list[Path] = []
    wt = rec.get("worktree_path") or worktree_path
    if isinstance(wt, str) and wt.strip():
        repo_candidates.append(Path(wt))
    if source_repo:
        repo_candidates.append(Path(source_repo))
    from services.git_integration_worker.cursor_sdk_git_head import (
        resolve_git_head,
        tip_window_meter_counts,
    )

    for repo_path in repo_candidates:
        if not repo_path.is_dir():
            continue
        closeout_head = resolve_git_head(repo_path)
        counts = tip_window_meter_counts(
            repo_path,
            dispatch_id=dispatch_id,
            admit_head=admit_head,
            closeout_head=closeout_head,
        )
        if counts is not None and counts[0] > 0:
            return True
    if source_repo:
        sidecar = Path(source_repo) / _SIDECAR_REL.format(dispatch_id=dispatch_id)
        if sidecar.is_file():
            ahead = _commits_ahead_from_text(sidecar.read_text(encoding="utf-8"))
            if ahead is not None and ahead > 0:
                return True
    return False


def _production_ledger_path() -> Path:
    """Operator gateway ledger. Never ``Path.home()`` (dispatch HOME overlay)."""
    from services.git_integration_worker.cursor_home import operator_real_home

    return operator_real_home() / ".gateway" / "cursor-sdk-dispatch.db"


def _process_ledger_path() -> Path | None:
    """Ledger path this process would open, or None when that is already production.

    ``CURSOR_SDK_DISPATCH_LEDGER`` wins, else ``DATA_DIR``. Unset both means
    ``CursorDispatchLedger.instance()`` already uses ``operator_real_home()``.
    """
    pinned = os.environ.get("CURSOR_SDK_DISPATCH_LEDGER", "").strip()
    if pinned:
        return Path(pinned).expanduser()
    data_dir = os.environ.get("DATA_DIR", "").strip()
    if data_dir:
        return Path(data_dir).expanduser() / "cursor-sdk-dispatch.db"
    return None


def _pytest_blocks_live_redirect(prod: Path) -> bool:
    """Do not retarget a pytest process onto the real passwd gateway db."""
    if not os.environ.get("PYTEST_CURRENT_TEST"):
        return False
    import pwd

    live = (
        Path(pwd.getpwuid(os.getuid()).pw_dir) / ".gateway" / "cursor-sdk-dispatch.db"
    )
    try:
        return prod.resolve() == live.resolve()
    except OSError:
        return False


def witness_ledger_path() -> Path | None:
    """Production ledger when this process would open a different db.

    Stargate sets ``DATA_DIR=/tmp``. A cursor-sdk dispatch sets ``HOME`` to an
    overlay and may point ``DATA_DIR`` at that overlay. Nested implement rows
    live in the operator gateway ledger, not ``Path.home()``.
    """
    prod = _production_ledger_path()
    if not prod.is_file():
        return None
    if _pytest_blocks_live_redirect(prod):
        return None
    process = _process_ledger_path()
    if process is None:
        return None
    try:
        if process.resolve() == prod.resolve():
            return None
    except OSError:
        return prod
    return prod


def _ledger_for_witness() -> Any:
    from services.git_integration_worker.cursor_dispatch_ledger import (
        CursorDispatchLedger,
    )

    home_db = witness_ledger_path()
    if home_db is None:
        return CursorDispatchLedger.instance()
    # Skip ``__init__`` so a read-only witness does not run DDL on the live db.
    ledger = CursorDispatchLedger.__new__(CursorDispatchLedger)
    ledger._tasks = {}
    ledger._db_path = home_db
    return ledger


def _row_has_commits(row: Any) -> bool:
    return _nested_child_has_commits(
        dispatch_id=str(row["dispatch_id"]),
        contract=row["contract"],
        status=row["status"],
        record_json=row["record_json"],
        wt_baseline=row["wt_baseline"],
        source_repo=row["source_repo"],
        worktree_path=None,
    )


def _nested_descendant_has_commits(
    ledger: Any,
    *,
    parent_dispatch_id: str,
    seen: set[str],
    depth: int,
) -> bool:
    if depth >= _MAX_NEST_WALK or parent_dispatch_id in seen:
        return False
    seen.add(parent_dispatch_id)
    child_ids = ledger.list_nested_children(parent_dispatch_id=parent_dispatch_id)
    if not child_ids:
        return False
    with ledger._connect() as conn:
        for child_id in child_ids:
            row = conn.execute(
                "SELECT dispatch_id, contract, status, record_json, wt_baseline, "
                "source_repo FROM cursor_sdk_dispatches "
                "WHERE dispatch_id=?",
                (child_id,),
            ).fetchone()
            if row is None:
                continue
            if _row_has_commits(row):
                return True
            if _nested_descendant_has_commits(
                ledger,
                parent_dispatch_id=str(row["dispatch_id"]),
                seen=seen,
                depth=depth + 1,
            ):
                return True
    return False


def _resume_of_children_have_commits(ledger: Any, *, parent_dispatch_id: str) -> bool:
    import sqlite3

    try:
        with ledger._connect() as conn:
            rows = conn.execute(
                "SELECT dispatch_id, contract, status, record_json, wt_baseline, "
                "source_repo FROM cursor_sdk_dispatches WHERE resume_of=?",
                (parent_dispatch_id,),
            ).fetchall()
    except sqlite3.OperationalError:
        return False
    for row in rows:
        if _row_has_commits(row):
            return True
    return False


def nested_implement_has_commits(*, nest_under_dispatch_id: str) -> bool:
    """True when a terminal nested implement child authored commits (SF1)."""
    ledger = _ledger_for_witness()
    if _nested_descendant_has_commits(
        ledger, parent_dispatch_id=nest_under_dispatch_id, seen=set(), depth=0
    ):
        return True
    return _resume_of_children_have_commits(
        ledger, parent_dispatch_id=nest_under_dispatch_id
    )


def _dispatch_ids_on_thread(ledger: Any, thread_id: str) -> list[str]:
    with ledger._connect() as conn:
        rows = conn.execute(
            "SELECT dispatch_id FROM cursor_sdk_dispatches WHERE thread_id=?",
            (thread_id,),
        ).fetchall()
    return [str(row["dispatch_id"]) for row in rows]


def parent_ids_for_tip(
    ledger: Any,
    tip_body: str,
    explicit_parent_id: str | None,
) -> list[str]:
    """Parent ids the G5 witness should try, explicit id first.

    The scoreboard names the hop-1 conductor (``dispatch `<id>` on thread N``).
    The nested child that authored the commits may sit under a later dispatch
    on that same thread, which the hop-1 id does not nest.
    """
    ordered: list[str] = []
    if explicit_parent_id:
        ordered.append(explicit_parent_id)
    threads: list[str] = []
    for match in _DISPATCH_ON_THREAD_RE.finditer(tip_body or ""):
        dispatch_id, thread_id = match.group(1), match.group(2)
        if dispatch_id not in ordered:
            ordered.append(dispatch_id)
        if thread_id not in threads:
            threads.append(thread_id)
    for thread_id in threads:
        for dispatch_id in _dispatch_ids_on_thread(ledger, thread_id):
            if dispatch_id not in ordered:
                ordered.append(dispatch_id)
    return ordered


def nested_parent_with_commits(
    *,
    tip_body: str,
    explicit_parent_id: str | None,
) -> str | None:
    """Return a parent dispatch id whose nested child authored commits."""
    ledger = _ledger_for_witness()
    for parent_id in parent_ids_for_tip(ledger, tip_body, explicit_parent_id):
        if nested_implement_has_commits(nest_under_dispatch_id=parent_id):
            return parent_id
    return None


class LedgerNestedImplementWitness:
    """FoldDeps adapter — wires GIW ledger reads at the production boundary."""

    def nested_implement_has_commits(self, *, nest_under_dispatch_id: str) -> bool:
        return nested_implement_has_commits(
            nest_under_dispatch_id=nest_under_dispatch_id
        )

    def parent_with_commits(
        self,
        *,
        tip_body: str,
        explicit_parent_id: str | None,
    ) -> str | None:
        return nested_parent_with_commits(
            tip_body=tip_body,
            explicit_parent_id=explicit_parent_id,
        )


def fold_deps_with_ledger(
    source_ref: str,
    *,
    repo: Path,
    summon_mode: str | None = None,
    summoning_thread_id: str | None = None,
) -> Any:
    """Fold readers for every process that folds, including the production ledger."""
    from implement_admission.conductor_witness_defaults import (
        DefaultWitnessCortex,
        fold_deps_for_admit,
    )

    return fold_deps_for_admit(
        source_ref,
        cortex=DefaultWitnessCortex(),
        repo=repo,
        summon_mode=summon_mode,
        summoning_thread_id=summoning_thread_id,
        nested_implement=LedgerNestedImplementWitness(),
    )
