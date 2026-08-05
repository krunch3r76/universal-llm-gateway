"""Lane-A authored-path attribution and tree residue derivation."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from services.git_integration_worker.cursor_dispatch_ledger import (
    CursorDispatchLedger,
)
from services.git_integration_worker.cursor_sdk_closeout import (
    capture_wt_baseline,
    changed_paths,
)
from services.git_integration_worker.cursor_sdk_git_head import (
    observed_lane_git_refs,
    paths_in_commit,
    resolve_git_head,
)
from services.git_integration_worker.seat_write_ledger import SeatWriteLedger

_TREE_RESIDUE_RE = re.compile(r"(?im)^tree_residue:\s*(\d+)\b")
_CHECKPOINT_LINE_RE = re.compile(r"(?im)^checkpoint:\s*(.+)$")
_BOLD_CHECKPOINT_RE = re.compile(r"(?im)^\*\*checkpoint:\*\*\s*(.+)$")


@dataclass(frozen=True)
class AuthoredPathProbe:
    """Probe answer: dispatch baseline → exact authored-path set."""

    exact_at_dispatch: bool
    covers_nested_cursor_sdk: bool
    covers_attended_composer: bool
    registration_mechanism: str
    detail: str


@dataclass(frozen=True)
class TreeResidueSnapshot:
    """Derived dirty-tree residue vs an episode authored-path set."""

    count: int
    authored_paths: tuple[str, ...]


def probe_authored_path_baseline() -> AuthoredPathProbe:
    """Record the code-verified probe answer for lane-A baseline attribution."""
    return AuthoredPathProbe(
        exact_at_dispatch=True,
        covers_nested_cursor_sdk=True,
        covers_attended_composer=True,
        registration_mechanism=(
            "Lane B: Cursor ``afterFileEdit`` hook → "
            "``scripts/cursor/register_seat_write.py`` → "
            "``SeatWriteLedger.register_paths`` (SQLite at "
            "``DATA_DIR/seat-write-ledger.db``). Arc opened on ``sessionStart``, "
            "closed on ``sessionEnd``. GIW ``lane_b_sweeper_loop`` commits "
            "closed-arc quiescent registered paths only."
        ),
        detail=(
            "Per-dispatch admit ``wt_baseline`` yields exact authored paths for "
            "cursor-sdk episodes (lane A). Attended IDE/Composer writes register "
            "via the hook at edit time (lane B); ``tree_residue`` counts only "
            "dirty paths in neither set — registration gaps, not WIP to respect."
        ),
    )


def authored_paths_for_dispatch(
    *,
    source_repo: Path,
    dispatch_id: str,
) -> tuple[str, ...]:
    """Return paths attributed to one dispatch via its admit baseline."""
    baseline = CursorDispatchLedger.instance().read_wt_baseline(dispatch_id=dispatch_id)
    if baseline is None:
        return ()
    change_set, _deviations = changed_paths(source_repo, baseline)
    return tuple(
        dict.fromkeys((*change_set.created, *change_set.modified, *change_set.deleted))
    )


def derive_tree_residue(
    *,
    source_repo: Path,
    dispatch_id: str,
    baseline: dict[str, Any] | None = None,
) -> TreeResidueSnapshot:
    """Count dirty paths not attributable to lane-A or lane-B authorship."""
    if baseline is None:
        baseline = CursorDispatchLedger.instance().read_wt_baseline(
            dispatch_id=dispatch_id
        )
    if baseline is None:
        authored: set[str] = set()
    else:
        change_set, _deviations = changed_paths(source_repo, baseline)
        authored = set((*change_set.created, *change_set.modified, *change_set.deleted))
    registered = SeatWriteLedger.instance().registered_paths(
        source_repo=str(source_repo.resolve())
    )
    attributed = authored | set(registered)
    current = capture_wt_baseline(source_repo) or {}
    dirty_now = set(current.keys())
    residue_count = len(dirty_now - attributed)
    return TreeResidueSnapshot(
        count=residue_count,
        authored_paths=tuple(sorted(authored)),
    )


def inject_tree_residue_line(body: str, *, count: int) -> str:
    """Replace or append infrastructure-derived ``tree_residue:`` on a CLOSEOUT."""
    line = f"tree_residue: {count}"
    if _TREE_RESIDUE_RE.search(body):
        return _TREE_RESIDUE_RE.sub(line, body, count=1)
    status_match = re.search(r"(?im)^status:\s*\S+\s*$", body)
    if status_match is None:
        return body.rstrip() + f"\n{line}\n"
    insert_at = status_match.end()
    return f"{body[:insert_at]}\n{line}{body[insert_at:]}"


def extract_authored_checkpoint(body: str) -> str | None:
    """Return the checkpoint disposition value from executor-authored closeout prose."""
    from claude_bundles.lane_a_closeout_checkpoint import normalize_checkpoint_value

    from services.git_integration_worker.cursor_auto.closeout_relay_cortex_fields import (
        extract_field_section,
        fenced_spans,
        in_fenced_span,
    )

    text = body or ""
    fenced = fenced_spans(text)
    for match in _CHECKPOINT_LINE_RE.finditer(text):
        if not in_fenced_span(fenced, match.start()):
            return normalize_checkpoint_value(match.group(1))
    for match in _BOLD_CHECKPOINT_RE.finditer(text):
        if not in_fenced_span(fenced, match.start()):
            return normalize_checkpoint_value(match.group(1))

    section = extract_field_section(text, "checkpoint")
    if section and section.strip():
        return normalize_checkpoint_value(section.strip())
    table_row_re = re.compile(r"(?im)^\|\s*checkpoint\s*\|\s*(?P<value>.*?)\s*\|")
    table_match = next(
        (
            match
            for match in table_row_re.finditer(text)
            if not in_fenced_span(fenced, match.start())
        ),
        None,
    )
    if table_match is not None:
        value = table_match.group("value").strip()
        if value and not value.casefold().startswith("relay could not locate"):
            return normalize_checkpoint_value(value)
    return None


def _parse_wt_baseline(raw: Any) -> dict[str, Any] | None:
    if raw is None:
        return None
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str) and raw.strip():
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return None
        return parsed if isinstance(parsed, dict) else None
    return None


def compute_lane_a_checkpoint_value(
    *,
    source_repo: Path,
    dispatch_id: str,
    baseline: dict[str, Any] | None = None,
) -> str:
    """Infrastructure-derived checkpoint disposition — not agent-typed."""
    if baseline is None:
        baseline = _parse_wt_baseline(
            CursorDispatchLedger.instance().read_wt_baseline(dispatch_id=dispatch_id)
        )
    authored = authored_paths_for_dispatch(
        source_repo=source_repo,
        dispatch_id=dispatch_id,
    )
    admit_head = None
    if isinstance(baseline, dict):
        raw_admit = baseline.get("admit_head")
        if isinstance(raw_admit, str) and raw_admit.strip():
            admit_head = raw_admit.strip()
    closeout_head = resolve_git_head(source_repo)
    lane_refs = observed_lane_git_refs(
        source_repo,
        dispatch_id=dispatch_id,
        admit_head=admit_head,
        closeout_head=closeout_head,
    )
    if lane_refs:
        sha = lane_refs[0]
        path_count = len(paths_in_commit(source_repo, sha))
        return f"committed {sha} paths={path_count}"
    if authored:
        return "deferred: authored paths not yet path-explicit committed"
    return "nothing_authored"


def inject_checkpoint_line(body: str, *, value: str) -> str:
    """Replace or append executor-authored ``checkpoint:`` for lane-A validation."""
    line = f"checkpoint: {value}"
    if _CHECKPOINT_LINE_RE.search(body):
        return _CHECKPOINT_LINE_RE.sub(line, body, count=1)
    residue_match = _TREE_RESIDUE_RE.search(body)
    if residue_match:
        insert_at = residue_match.end()
        return f"{body[:insert_at]}\n{line}{body[insert_at:]}"
    status_match = re.search(r"(?im)^status:\s*\S+\s*$", body)
    if status_match is None:
        return body.rstrip() + f"\n{line}\n"
    insert_at = status_match.end()
    return f"{body[:insert_at]}\n{line}{body[insert_at:]}"


__all__ = [
    "AuthoredPathProbe",
    "TreeResidueSnapshot",
    "authored_paths_for_dispatch",
    "compute_lane_a_checkpoint_value",
    "derive_tree_residue",
    "extract_authored_checkpoint",
    "inject_checkpoint_line",
    "inject_tree_residue_line",
    "probe_authored_path_baseline",
]
