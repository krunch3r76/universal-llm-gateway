"""Measure how far a lane branch has drifted from master — merge hazard, not land verdict."""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

_GIT_TIMEOUT_S = 60.0


@dataclass(frozen=True, slots=True)
class BranchDivergence:
    """How far ``branch_name`` has drifted from ``master`` — observational only."""

    behind_by: int
    ahead_by: int
    files_changed: int
    insertions: int
    deletions: int
    measured: bool

    def describe(self) -> str:
        """One-line merge-hazard summary for operators."""
        if not self.measured:
            return "divergence unmeasured"
        if self.behind_by <= 0 and self.files_changed == 0:
            return "level with master"
        parts: list[str] = []
        if self.behind_by > 0:
            parts.append(f"behind master by {self.behind_by} commits")
        if self.files_changed > 0:
            parts.append(
                f"merging would touch {self.files_changed} files "
                f"(+{self.insertions}/-{self.deletions})"
            )
        return "; ".join(parts) or "level with master"


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        timeout=_GIT_TIMEOUT_S,
        check=False,
    )


def _parse_shortstat(text: str) -> tuple[int, int, int]:
    line = text.strip()
    if not line:
        return (0, 0, 0)
    files_changed = 0
    insertions = 0
    deletions = 0
    if m := re.search(r"(\d+) files? changed", line):
        files_changed = int(m.group(1))
    if m := re.search(r"(\d+) insertions?\(\+\)", line):
        insertions = int(m.group(1))
    if m := re.search(r"(\d+) deletions?\(-\)", line):
        deletions = int(m.group(1))
    return files_changed, insertions, deletions


def _rev_list_count(repo: Path, rev_range: str) -> int | None:
    proc = _git(repo, "rev-list", "--count", rev_range)
    if proc.returncode != 0:
        return None
    try:
        return int(proc.stdout.strip())
    except ValueError:
        return None


def measure_divergence(*, repo: Path, branch_name: str) -> BranchDivergence:
    """Fail-soft divergence measurement — never raises into discharge paths."""
    root = repo.resolve()
    behind = _rev_list_count(root, f"{branch_name}..master")
    ahead = _rev_list_count(root, f"master..{branch_name}")
    stat = _git(root, "diff", "--shortstat", f"master..{branch_name}")
    if behind is None or ahead is None or stat.returncode != 0:
        return BranchDivergence(
            behind_by=0,
            ahead_by=0,
            files_changed=0,
            insertions=0,
            deletions=0,
            measured=False,
        )
    files_changed, insertions, deletions = _parse_shortstat(stat.stdout)
    return BranchDivergence(
        behind_by=behind,
        ahead_by=ahead,
        files_changed=files_changed,
        insertions=insertions,
        deletions=deletions,
        measured=True,
    )
