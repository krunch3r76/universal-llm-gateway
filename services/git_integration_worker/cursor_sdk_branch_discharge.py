"""One-call branch discharge — the clean path must be cheaper than residue.

A lane retires its branch one of two honest ways: ``landed`` (the work is on
master, verified by probing content rather than trusting the narrative) or
``discard`` (deliberately abandoned, with a recorded reason). Both archive the
tip first, so neither is destructive.

``landed`` is measured, not asserted. A closeout claiming a land that the tree
does not show is refused and told which paths disagree — the same posture
``cursor_sdk_land_discipline`` already takes on the closeout grade.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from universal_logging import get_logger

from services.git_integration_worker.cursor_sdk_branch_archive import (
    archive_branch,
    branch_checked_out_at,
)
from services.git_integration_worker.cursor_sdk_branch_debt import (
    discharge_branch_debt,
    get_branch_debt,
)
from services.git_integration_worker.cursor_sdk_events import (
    emit_sdk_lane_b_discharged,
)

logger = get_logger(__name__)

_GIT_TIMEOUT_S = 60.0
DISCHARGE_LANDED = "landed"
DISCHARGE_DISCARD = "discard"


@dataclass(frozen=True, slots=True)
class LandProbe:
    """Whether every path a branch touched is represented on master."""

    landed: bool
    differing_paths: list[str] = field(default_factory=list)
    missing_paths: list[str] = field(default_factory=list)

    def describe(self) -> str:
        """One-line reason naming the paths that block a landed claim."""
        parts: list[str] = []
        if self.missing_paths:
            parts.append(f"absent from master: {', '.join(self.missing_paths)}")
        if self.differing_paths:
            parts.append(f"content differs: {', '.join(self.differing_paths)}")
        return "; ".join(parts) or "landed"


@dataclass(frozen=True, slots=True)
class DischargeResult:
    """Outcome of a discharge attempt."""

    discharged: bool
    branch: str
    verb: str
    tip_sha: str | None = None
    archive_tag: str | None = None
    refused_reason: str | None = None
    probe: LandProbe | None = None


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        timeout=_GIT_TIMEOUT_S,
        check=False,
    )


def _blob_sha(repo: Path, ref: str, path: str) -> str | None:
    proc = _git(repo, "rev-parse", f"{ref}:{path}")
    return proc.stdout.strip() if proc.returncode == 0 else None


def _content_lines(repo: Path, ref: str, path: str) -> set[str] | None:
    proc = _git(repo, "show", f"{ref}:{path}")
    if proc.returncode != 0:
        return None
    return {line.strip() for line in proc.stdout.splitlines() if line.strip()}


def probe_landed(*, repo: Path, branch_name: str) -> LandProbe:
    """Check every path the branch touched against master.

    A path passes when master's blob is identical, or when master's content is a
    superset of the branch's — the shape left after the lead lands lane work from
    the shared checkout and master then evolves past it. Anything else is named,
    because an unverifiable land claim is exactly what this gate exists to catch.
    """
    root = repo.resolve()
    base = _git(root, "merge-base", "master", branch_name)
    if base.returncode != 0:
        return LandProbe(
            landed=False, differing_paths=[f"{branch_name} (no merge-base)"]
        )
    merge_base = base.stdout.strip()

    changed = _git(root, "diff", "--name-only", f"{merge_base}..{branch_name}")
    if changed.returncode != 0:
        return LandProbe(landed=False, differing_paths=[f"{branch_name} (diff failed)"])
    paths = [line.strip() for line in changed.stdout.splitlines() if line.strip()]
    if not paths:
        return LandProbe(landed=True)

    differing: list[str] = []
    missing: list[str] = []
    for path in paths:
        branch_blob = _blob_sha(root, branch_name, path)
        master_blob = _blob_sha(root, "master", path)
        if branch_blob is None:
            # Branch deleted the path; landed only if master dropped it too.
            if master_blob is not None:
                differing.append(path)
            continue
        if master_blob is None:
            missing.append(path)
            continue
        if branch_blob == master_blob:
            continue
        branch_lines = _content_lines(root, branch_name, path)
        master_lines = _content_lines(root, "master", path)
        if branch_lines is None or master_lines is None:
            differing.append(path)
            continue
        if not branch_lines <= master_lines:
            differing.append(path)
    return LandProbe(
        landed=not differing and not missing,
        differing_paths=differing,
        missing_paths=missing,
    )


def _delete_branch(repo: Path, branch_name: str) -> tuple[bool, str | None]:
    pinned = branch_checked_out_at(repo=repo, branch_name=branch_name)
    if pinned is not None:
        return False, f"branch checked out at {pinned}"
    proc = _git(repo, "branch", "-D", branch_name)
    if proc.returncode != 0:
        return False, proc.stderr.strip() or "git branch -D failed"
    return True, None


def _finish(
    *,
    repo: Path,
    branch_name: str,
    verb: str,
    note: str | None,
) -> DischargeResult:
    """Archive, delete, retire the debt, and announce — shared by both verbs."""
    root = repo.resolve()
    rev = _git(root, "rev-parse", "--verify", f"{branch_name}^{{commit}}")
    tip_sha = rev.stdout.strip() if rev.returncode == 0 else None

    archive_tag = archive_branch(repo=root, branch_name=branch_name)
    if archive_tag is None:
        return DischargeResult(
            discharged=False,
            branch=branch_name,
            verb=verb,
            tip_sha=tip_sha,
            refused_reason="archive failed — refusing to delete an unarchived tip",
        )

    deleted, error = _delete_branch(root, branch_name)
    if not deleted:
        return DischargeResult(
            discharged=False,
            branch=branch_name,
            verb=verb,
            tip_sha=tip_sha,
            archive_tag=archive_tag,
            refused_reason=error,
        )

    debt = get_branch_debt(branch_name=branch_name)
    discharge_branch_debt(branch_name=branch_name, verb=verb, note=note)
    _clear_disposition(branch_name)
    emit_sdk_lane_b_discharged(
        branch=branch_name,
        verb=verb,
        tip_sha=tip_sha,
        archive_tag=archive_tag,
        thread_id=debt.thread_id if debt is not None else None,
        note=note,
    )
    logger.info(
        "lane_b branch discharged branch=%s verb=%s archive=%s",
        branch_name,
        verb,
        archive_tag,
    )
    return DischargeResult(
        discharged=True,
        branch=branch_name,
        verb=verb,
        tip_sha=tip_sha,
        archive_tag=archive_tag,
    )


def _clear_disposition(branch_name: str) -> None:
    from services.git_integration_worker.cursor_sdk_lane_b_disposition import (
        clear_disposition,
    )

    clear_disposition(branch_name=branch_name)


def discharge_landed(*, repo: Path, branch_name: str) -> DischargeResult:
    """Retire a branch whose work is verifiably on master."""
    probe = probe_landed(repo=repo, branch_name=branch_name)
    if not probe.landed:
        logger.warning(
            "lane_b landed discharge refused branch=%s reason=%s",
            branch_name,
            probe.describe(),
        )
        return DischargeResult(
            discharged=False,
            branch=branch_name,
            verb=DISCHARGE_LANDED,
            refused_reason=f"not landed — {probe.describe()}",
            probe=probe,
        )
    result = _finish(
        repo=repo,
        branch_name=branch_name,
        verb=DISCHARGE_LANDED,
        note=None,
    )
    return DischargeResult(
        discharged=result.discharged,
        branch=result.branch,
        verb=result.verb,
        tip_sha=result.tip_sha,
        archive_tag=result.archive_tag,
        refused_reason=result.refused_reason,
        probe=probe,
    )


def discharge_discard(*, repo: Path, branch_name: str, reason: str) -> DischargeResult:
    """Retire a branch the lane deliberately abandons; reason is recorded."""
    if not reason.strip():
        return DischargeResult(
            discharged=False,
            branch=branch_name,
            verb=DISCHARGE_DISCARD,
            refused_reason="discard requires a reason",
        )
    return _finish(
        repo=repo,
        branch_name=branch_name,
        verb=DISCHARGE_DISCARD,
        note=reason.strip(),
    )


def discharge(
    *,
    repo: Path,
    branch_name: str,
    verb: str,
    reason: str | None = None,
) -> DischargeResult:
    """Dispatch on the declared verb — the single entry point a closeout names."""
    normalized = (verb or "").strip().lower()
    if normalized == DISCHARGE_LANDED:
        return discharge_landed(repo=repo, branch_name=branch_name)
    if normalized == DISCHARGE_DISCARD:
        return discharge_discard(
            repo=repo,
            branch_name=branch_name,
            reason=reason or "",
        )
    return DischargeResult(
        discharged=False,
        branch=branch_name,
        verb=normalized or "(unset)",
        refused_reason=(
            f"unknown land_disposition {normalized!r} — "
            f"expected {DISCHARGE_LANDED!r} or {DISCHARGE_DISCARD!r}"
        ),
    )
