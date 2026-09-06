"""Nested implement commit witness for conductor G5 (SF1)."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

_IMPLEMENT_CONTRACTS = frozenset({"implement", "pure-mechanical"})
_TERMINAL_STATUSES = frozenset({"completed", "failed", "cancelled"})
_COMMITS_AHEAD_RE = re.compile(
    r'(?i)(?:^|[,{])\s*"commits_ahead"\s*:\s*(\d+)'
)
_SIDECAR_REL = "tmp/reviews/closeouts/{dispatch_id}.md"


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
    if str(contract or "") not in _IMPLEMENT_CONTRACTS:
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


def nested_implement_has_commits(*, nest_under_dispatch_id: str) -> bool:
    """True when a terminal nested implement child authored commits (SF1)."""
    from services.git_integration_worker.cursor_dispatch_ledger import (
        CursorDispatchLedger,
    )

    ledger = CursorDispatchLedger.instance()
    child_ids = ledger.list_nested_children(parent_dispatch_id=nest_under_dispatch_id)
    if not child_ids:
        return False
    with ledger._connect() as conn:
        for child_id in child_ids:
            row = conn.execute(
                "SELECT dispatch_id, contract, status, record_json, wt_baseline, "
                "source_repo, worktree_path FROM cursor_sdk_dispatches "
                "WHERE dispatch_id=?",
                (child_id,),
            ).fetchone()
            if row is None:
                continue
            if _nested_child_has_commits(
                dispatch_id=str(row["dispatch_id"]),
                contract=row["contract"],
                status=row["status"],
                record_json=row["record_json"],
                wt_baseline=row["wt_baseline"],
                source_repo=row["source_repo"],
                worktree_path=row["worktree_path"],
            ):
                return True
    return False
