"""Lane-A authored-path attribution and tree residue derivation."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from services.git_integration_worker.cursor_dispatch_ledger import (
    CursorDispatchLedger,
)
from services.git_integration_worker.seat_write_ledger import SeatWriteLedger
from services.git_integration_worker.cursor_sdk_closeout import (
    capture_wt_baseline,
    changed_paths,
)

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
    baseline = CursorDispatchLedger.instance().read_wt_baseline(
        dispatch_id=dispatch_id
    )
    if baseline is None:
        return ()
    change_set, _deviations = changed_paths(source_repo, baseline)
    return tuple(
        dict.fromkeys(
            (*change_set.created, *change_set.modified, *change_set.deleted)
        )
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
        authored = set(
            (*change_set.created, *change_set.modified, *change_set.deleted)
        )
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
    text = body or ""
    match = _CHECKPOINT_LINE_RE.search(text)
    if match:
        return match.group(1).strip()
    bold = _BOLD_CHECKPOINT_RE.search(text)
    if bold:
        return bold.group(1).strip()
    from services.git_integration_worker.cursor_auto.closeout_relay_cortex_fields import (
        extract_field_section,
    )

    section = extract_field_section(text, "checkpoint")
    if section and section.strip():
        return section.strip()
    table_match = re.search(
        r"(?im)^\|\s*checkpoint\s*\|\s*(?P<value>.*?)\s*\|",
        text,
    )
    if table_match:
        value = table_match.group("value").strip()
        if value and not value.casefold().startswith("relay could not locate"):
            return value
    return None


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
    "derive_tree_residue",
    "extract_authored_checkpoint",
    "inject_checkpoint_line",
    "inject_tree_residue_line",
    "probe_authored_path_baseline",
]
