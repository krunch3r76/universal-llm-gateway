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

import os
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from universal_logging import get_logger

from services.git_integration_worker import (
    cursor_sdk_branch_debt_reconcile,
    cursor_sdk_branch_divergence,
)
from services.git_integration_worker.cursor_sdk_branch_archive import (
    archive_branch,
    branch_checked_out_at,
    lookup_archive_tag,
)
from services.git_integration_worker.cursor_sdk_branch_debt import (
    discharge_branch_debt,
    get_branch_debt,
)
from services.git_integration_worker.cursor_sdk_branch_debt_tags import (
    remove_land_required_tag,
)
from services.git_integration_worker.cursor_sdk_branch_divergence import (
    BranchDivergence,
)
from services.git_integration_worker.cursor_sdk_events import (
    emit_sdk_lane_b_discharged,
    emit_sdk_lane_b_reaped,
)

logger = get_logger(__name__)

_GIT_TIMEOUT_S = 60.0
_PATCH_ID_NOTE_MAX = 512
DISCHARGE_LANDED = "landed"
DISCHARGE_DISCARD = "discard"
DISCHARGE_UNLANDED = "unlanded"


@dataclass(frozen=True, slots=True)
class LandProbe:
    """Whether every path a branch touched is represented on master."""

    landed: bool
    differing_paths: list[str] = field(default_factory=list)
    missing_paths: list[str] = field(default_factory=list)
    divergence: BranchDivergence | None = None
    patch_id_note: str | None = None

    def describe(self) -> str:
        """One-line reason naming the paths that block a landed claim."""
        parts: list[str] = []
        if self.missing_paths:
            parts.append(f"absent from master: {', '.join(self.missing_paths)}")
        if self.differing_paths:
            parts.append(f"content differs: {', '.join(self.differing_paths)}")
        if not parts:
            if (
                self.divergence is not None
                and self.divergence.measured
                and self.divergence.behind_by > 0
            ):
                return (
                    f"landed (but behind master by {self.divergence.behind_by} commits "
                    "— hand-port, do not merge)"
                )
            return "landed"
        result = "; ".join(parts)
        if (
            self.divergence is not None
            and self.divergence.measured
            and self.divergence.behind_by > 0
        ):
            result = f"{result}; {self.divergence.describe()}"
        return result


@dataclass(frozen=True, slots=True)
class DischargeResult:
    """Outcome of a discharge attempt."""

    discharged: bool
    branch: str
    verb: str
    tip_sha: str | None = None
    archive_tag: str | None = None
    archive_skipped_reason: str | None = None
    refused_reason: str | None = None
    probe: LandProbe | None = None
    inherited: bool = False
    worktree_release: str | None = None


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


def _format_patch_id_note(pairs: list[tuple[str, str]]) -> str | None:
    if not pairs:
        return None
    body = "patch-id:" + ",".join(f"{branch}={master}" for branch, master in pairs)
    if len(body) <= _PATCH_ID_NOTE_MAX:
        return body
    return body[: _PATCH_ID_NOTE_MAX - 1] + "…"


def _patch_id_lines(diff_text: str) -> list[tuple[str, str]]:
    if not diff_text.strip():
        return []
    proc = subprocess.run(
        ["git", "patch-id", "--verbatim"],
        input=diff_text,
        capture_output=True,
        text=True,
        timeout=_GIT_TIMEOUT_S,
        check=False,
    )
    if proc.returncode != 0:
        return []
    rows: list[tuple[str, str]] = []
    for line in proc.stdout.splitlines():
        parts = line.split()
        if len(parts) >= 2:
            rows.append((parts[0], parts[1]))
        elif len(parts) == 1:
            rows.append((parts[0], ""))
    return rows


def _commit_patch_id(repo: Path, commit: str) -> str | None:
    log = _git(repo, "log", "-1", "-p", "--no-renames", "--format=%H", commit)
    if log.returncode != 0:
        return None
    rows = _patch_id_lines(log.stdout)
    return rows[0][0] if rows else None


def _inverse_patch_id(repo: Path, commit: str) -> str | None:
    """Patch-id of ``git diff --no-renames E E^`` — the inverse of E."""
    diff = _git(repo, "diff", "--no-renames", commit, f"{commit}^")
    if diff.returncode != 0:
        return None
    rows = _patch_id_lines(diff.stdout)
    return rows[0][0] if rows else None


def _master_patch_index(repo: Path, merge_base: str) -> dict[str, list[str]]:
    """One pass: patch-id → master shas in ``merge_base..master``."""
    log = _git(
        repo,
        "log",
        "-p",
        "--no-renames",
        "--format=%H",
        f"{merge_base}..master",
    )
    if log.returncode != 0:
        return {}
    index: dict[str, list[str]] = {}
    for patch_id, sha in _patch_id_lines(log.stdout):
        if not sha:
            continue
        index.setdefault(patch_id, []).append(sha)
    return index


def _is_ancestor(repo: Path, ancestor: str, descendant: str) -> bool:
    proc = _git(repo, "merge-base", "--is-ancestor", ancestor, descendant)
    return proc.returncode == 0


def _commit_is_empty(repo: Path, commit: str) -> bool:
    """Empty only when ``git diff --quiet C^ C`` succeeds.

    ``--ignore-all-space`` hid real edits inside tokens (review S7).
    """
    parent = _git(repo, "rev-parse", "--verify", f"{commit}^")
    if parent.returncode != 0:
        return False
    quiet = _git(repo, "diff", "--quiet", parent.stdout.strip(), commit)
    return quiet.returncode == 0


def _commit_parent_count(repo: Path, commit: str) -> int:
    proc = _git(repo, "rev-list", "--parents", "-1", commit)
    if proc.returncode != 0:
        return 0
    parts = proc.stdout.strip().split()
    return max(0, len(parts) - 1)


def _paths_touched_by_commit(repo: Path, commit: str) -> set[str]:
    proc = _git(repo, "diff-tree", "--no-commit-id", "--name-only", "-r", commit)
    if proc.returncode != 0:
        return set()
    return {ln.strip() for ln in proc.stdout.splitlines() if ln.strip()}


def _parse_cherry_markers(repo: Path, branch_name: str) -> tuple[dict[str, str], bool]:
    proc = _git(repo, "cherry", "master", branch_name)
    if proc.returncode != 0:
        return {}, False
    markers: dict[str, str] = {}
    for line in proc.stdout.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        marker = stripped[0]
        if marker not in {"+", "-"}:
            continue
        abbrev = stripped[1:].strip()
        full = _git(repo, "rev-parse", "--verify", abbrev)
        if full.returncode != 0:
            continue
        markers[full.stdout.strip()] = marker
    return markers, bool(markers)


def _cherry_marker(
    commit: str, markers: dict[str, str], *, markers_nonempty: bool
) -> str:
    if commit in markers:
        return markers[commit]
    # Empty ``git cherry`` output means every branch commit is patch-equivalent.
    if not markers_nonempty:
        return "-"
    return "+"


def _match_is_void(
    repo: Path, equivalent_sha: str, index: dict[str, list[str]]
) -> bool:
    """Revert trailer or a later master commit whose patch-id is the inverse."""
    needle = f"This reverts commit {equivalent_sha}"
    proc = _git(repo, "log", "master", f"--grep={needle}", "--format=%H")
    if proc.returncode == 0 and proc.stdout.strip():
        return True
    inverse_id = _inverse_patch_id(repo, equivalent_sha)
    if not inverse_id:
        return False
    return any(sha != equivalent_sha for sha in index.get(inverse_id, []))


def _verbatim_master_matches(
    repo: Path, branch_commit: str, index: dict[str, list[str]]
) -> list[str]:
    patch_id = _commit_patch_id(repo, branch_commit)
    if not patch_id:
        return []
    return index.get(patch_id, [])


def _git_step(cmd: object) -> str:
    """The git subcommand inside a ``TimeoutExpired`` command."""
    if isinstance(cmd, (list, tuple)):
        args = [str(part) for part in cmd]
    else:
        args = str(cmd).split()
    index = 1 if args and Path(args[0]).name == "git" else 0
    if index < len(args) and args[index] == "-C":
        index += 2
    if index < len(args):
        return args[index]
    return "git"


@dataclass(frozen=True, slots=True)
class _PatchIdWalk:
    all_substantive_survived: bool
    note_pairs: tuple[tuple[str, str], ...]
    paths_patch_only: frozenset[str]
    paths_voided_no_superset: frozenset[str]
    patch_id_note: str | None = None


def _merge_first_parent_paths(repo: Path, commit: str) -> set[str]:
    proc = _git(repo, "diff", "--name-only", f"{commit}^1", commit)
    if proc.returncode != 0:
        return set()
    return {ln.strip() for ln in proc.stdout.splitlines() if ln.strip()}


def _walk_patch_id_evidence(
    repo: Path, *, branch_name: str, merge_base: str
) -> _PatchIdWalk:
    cherry, cherry_nonempty = _parse_cherry_markers(repo, branch_name)
    rev = _git(repo, "rev-list", f"{merge_base}..{branch_name}")
    if rev.returncode != 0:
        return _PatchIdWalk(False, (), frozenset(), frozenset())
    has_cherry_minus = any(marker == "-" for marker in cherry.values())
    index = (
        _master_patch_index(repo, merge_base) if has_cherry_minus else {}
    )

    note_pairs: list[tuple[str, str]] = []
    path_surviving: set[str] = set()
    paths_voided_no_superset: set[str] = set()
    needs_content: set[str] = set()
    substantive_count = 0
    surviving_count = 0

    for commit in (ln.strip() for ln in rev.stdout.splitlines() if ln.strip()):
        paths = _paths_touched_by_commit(repo, commit)
        parents = _commit_parent_count(repo, commit)
        if parents > 1:
            if _is_ancestor(repo, commit, "master"):
                continue
            quiet = _git(repo, "diff", "--quiet", f"{commit}^1", commit)
            if quiet.returncode == 0:
                continue
            substantive_count += 1
            needs_content.update(_merge_first_parent_paths(repo, commit))
            continue

        if _commit_is_empty(repo, commit):
            continue

        substantive_count += 1
        marker = _cherry_marker(commit, cherry, markers_nonempty=cherry_nonempty)
        if marker == "+":
            needs_content.update(paths)
            continue

        matches = _verbatim_master_matches(repo, commit, index)
        if not matches:
            # Cherry ``-`` without a verbatim patch-id is whitespace-only
            # equivalence. The line probe decides; it is not a void.
            needs_content.update(paths)
            continue
        if any(_match_is_void(repo, sha, index) for sha in matches):
            paths_voided_no_superset.update(paths)
            continue

        surviving_count += 1
        note_pairs.append((commit, matches[0]))
        path_surviving.update(paths)

    paths_patch_only = path_surviving - paths_voided_no_superset - needs_content
    all_survived = substantive_count > 0 and surviving_count == substantive_count
    note = _format_patch_id_note(note_pairs) if paths_patch_only else None

    return _PatchIdWalk(
        all_substantive_survived=all_survived,
        note_pairs=tuple(note_pairs),
        paths_patch_only=frozenset(paths_patch_only),
        paths_voided_no_superset=frozenset(paths_voided_no_superset),
        patch_id_note=note,
    )


def _branch_ref_exists(repo: Path, branch_name: str) -> bool:
    proc = _git(repo, "rev-parse", "--verify", f"refs/heads/{branch_name}")
    return proc.returncode == 0


def _already_retired(*, repo: Path, branch_name: str) -> DischargeResult | None:
    """Idempotent landed when the ref is gone but a prior retire left evidence."""
    root = repo.resolve()
    if _branch_ref_exists(root, branch_name):
        return None
    archived = lookup_archive_tag(repo=root, branch_name=branch_name)
    debt = get_branch_debt(branch_name=branch_name)
    if archived is None and (debt is None or debt.open):
        return None
    tag_name, tip_sha = archived or (None, None)
    if tip_sha is None and debt is not None:
        tip_sha = debt.tip_sha
    if tag_name is None and tip_sha is not None:
        tag_name = f"archive/{branch_name}-{tip_sha[:8]}"
    return DischargeResult(
        discharged=True,
        branch=branch_name,
        verb=DISCHARGE_LANDED,
        tip_sha=tip_sha,
        archive_tag=tag_name,
        probe=LandProbe(landed=True),
    )


def _presence_against_master(
    repo: Path, branch_name: str, path: str
) -> str | None:
    """``missing`` or ``differing`` when presence disagrees; None when it holds.

    A path the branch still has must exist on master. A path the branch deleted
    must be absent on master. Blob bytes are not compared here.
    """
    branch_blob = _blob_sha(repo, branch_name, path)
    master_blob = _blob_sha(repo, "master", path)
    if branch_blob is None:
        if master_blob is not None:
            return "differing"
        return None
    if master_blob is None:
        return "missing"
    return None


def _blob_identity_or_absent(
    repo: Path, branch_name: str, path: str
) -> str | None:
    """Pass on identical blobs or both absent. No line-superset fallback."""
    branch_blob = _blob_sha(repo, branch_name, path)
    master_blob = _blob_sha(repo, "master", path)
    if branch_blob is None and master_blob is None:
        return None
    if branch_blob is not None and branch_blob == master_blob:
        return None
    if master_blob is None:
        return "missing"
    return "differing"


def probe_landed(*, repo: Path, branch_name: str) -> LandProbe:
    """Check every path the branch touched against master.

    Walk ``merge-base..branch``. A non-merge commit that ``git cherry`` marks
    ``-`` and whose ``git patch-id --verbatim`` equals an unvoided commit in
    ``merge-base..master`` skips the line-superset probe; the path must still
    exist on master when the branch has it, and be absent when the branch
    deleted it. A cherry ``-`` with no verbatim match, a ``+``, or a substantive
    merge stays on the line-superset probe. A revert trailer or an inverse
    patch-id is a void: identical blobs may pass, and the line-superset
    fallback does not.

    A path on that line probe passes when master's blob is identical, or when
    master's stripped lines are a superset of the branch's. Anything else is
    named, because an unverifiable land claim is exactly what this gate exists
    to catch.
    """
    try:
        return _probe_landed(repo=repo, branch_name=branch_name)
    except subprocess.TimeoutExpired as exc:
        step = _git_step(exc.cmd)
        return LandProbe(
            landed=False,
            differing_paths=[f"{branch_name} ({step} timeout)"],
        )


def _probe_landed(*, repo: Path, branch_name: str) -> LandProbe:
    root = repo.resolve()
    divergence = cursor_sdk_branch_divergence.measure_divergence(
        repo=root, branch_name=branch_name
    )
    if not _branch_ref_exists(root, branch_name):
        return LandProbe(
            landed=False,
            differing_paths=[f"{branch_name} (ref missing)"],
            divergence=divergence,
        )
    base = _git(root, "merge-base", "master", branch_name)
    if base.returncode != 0:
        return LandProbe(
            landed=False,
            differing_paths=[f"{branch_name} (no merge-base)"],
            divergence=divergence,
        )
    merge_base = base.stdout.strip()

    changed = _git(root, "diff", "--name-only", f"{merge_base}..{branch_name}")
    if changed.returncode != 0:
        return LandProbe(
            landed=False,
            differing_paths=[f"{branch_name} (diff failed)"],
            divergence=divergence,
        )
    paths = [line.strip() for line in changed.stdout.splitlines() if line.strip()]
    if not paths:
        return LandProbe(landed=True, divergence=divergence)

    patch_walk = _walk_patch_id_evidence(
        root, branch_name=branch_name, merge_base=merge_base
    )
    differing: list[str] = []
    missing: list[str] = []
    for path in paths:
        if path in patch_walk.paths_patch_only or patch_walk.all_substantive_survived:
            presence = _presence_against_master(root, branch_name, path)
            if presence == "missing":
                missing.append(path)
            elif presence == "differing":
                differing.append(path)
            continue
        if path in patch_walk.paths_voided_no_superset:
            identity = _blob_identity_or_absent(root, branch_name, path)
            if identity == "missing":
                missing.append(path)
            elif identity == "differing":
                differing.append(path)
            continue
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
        divergence=divergence,
        patch_id_note=patch_walk.patch_id_note,
    )


def _delete_branch(
    repo: Path, branch_name: str, *, dispatch_id: str = ""
) -> tuple[bool, str | None]:
    if not _branch_ref_exists(repo, branch_name):
        return True, None
    pinned = branch_checked_out_at(repo=repo, branch_name=branch_name)
    if pinned is not None:
        return False, f"branch checked out at {pinned}"
    proc = _git(repo, "branch", "-D", branch_name)
    if proc.returncode != 0:
        err = proc.stderr.strip() or "git branch -D failed"
        logger.warning(
            "lane_b branch delete failed branch=%s err=%s",
            branch_name,
            err,
        )
        emit_sdk_lane_b_reaped(
            dispatch_id=dispatch_id,
            branch_deleted=False,
            outcome="failed",
            branch=branch_name,
            reason=f"discharge_delete_failed:{err}",
        )
        return False, err
    return True, None


def _finish(
    *,
    repo: Path,
    branch_name: str,
    verb: str,
    note: str | None,
    completing_dispatch_id: str | None = None,
) -> DischargeResult:
    """Archive, delete, retire the debt, and announce — shared by both verbs."""
    from services.git_integration_worker.cursor_sdk_branch_unpin import (
        _record_for_branch,
    )
    from services.git_integration_worker.cursor_sdk_lane_inherit import (
        thread_has_inheritor,
    )
    from services.git_integration_worker.cursor_sdk_worktree_registry import (
        active_pin,
        release_pin,
    )
    from services.git_integration_worker.cursor_sdk_worktree_release import (
        release_lane_worktree,
    )

    root = repo.resolve()
    rev = _git(root, "rev-parse", "--verify", f"{branch_name}^{{commit}}")
    tip_sha = rev.stdout.strip() if rev.returncode == 0 else None
    worktree_release: str | None = None

    record = _record_for_branch(source_repo=root, branch_name=branch_name)
    if (
        record is not None
        and record.thread_id
        and thread_has_inheritor(
            record.thread_id,
            completing_dispatch_id=completing_dispatch_id,
        )
    ):
        return DischargeResult(
            discharged=False,
            branch=branch_name,
            verb=verb,
            tip_sha=tip_sha,
            refused_reason="inherited by successor",
            inherited=True,
        )

    if record is not None and record.thread_id:
        pin = active_pin(source_repo=root, thread_id=record.thread_id)
        pin_dispatch_id = (
            pin.dispatch_id if pin is not None else record.last_dispatch_id
        )
        if pin_dispatch_id:
            release_pin(
                source_repo=root,
                thread_id=record.thread_id,
                dispatch_id=pin_dispatch_id,
                release_reason=f"disposition:{verb}",
            )
        release = release_lane_worktree(
            source_repo=root,
            thread_id=record.thread_id,
            worktree_path=record.worktree_path,
            dispatch_id=record.last_dispatch_id,
            reason="discharge",
            allow_unharvested=True,
            ignore_dispatch_id=completing_dispatch_id,
        )
        if release.deferred_to_caller:
            worktree_release = "deferred_to_caller"
        elif not release.released:
            refused = release.refusal.value if release.refusal is not None else None
            if refused is None and release.salvage_refused:
                refused = "salvage_refused"
            elif refused is None and release.branch_retained:
                refused = "branch_retained"
            return DischargeResult(
                discharged=False,
                branch=branch_name,
                verb=verb,
                tip_sha=tip_sha,
                refused_reason=refused,
            )

    archive_tag = archive_branch(repo=root, branch_name=branch_name)
    archive_skipped_reason: str | None = None
    if archive_tag is None:
        probe_sha = tip_sha
        if not probe_sha:
            debt_for_probe = get_branch_debt(branch_name=branch_name)
            if debt_for_probe is not None:
                probe_sha = debt_for_probe.tip_sha
        if cursor_sdk_branch_debt_reconcile.commit_exists(root, probe_sha or ""):
            return DischargeResult(
                discharged=False,
                branch=branch_name,
                verb=verb,
                tip_sha=tip_sha or probe_sha,
                refused_reason=(
                    "archive failed — refusing to delete an unarchived tip"
                ),
                worktree_release=worktree_release,
            )
        archive_skipped_reason = "tip_unreachable"

    delete_dispatch_id = completing_dispatch_id or (
        record.last_dispatch_id if record is not None else None
    ) or ""
    deleted, error = _delete_branch(
        root, branch_name, dispatch_id=delete_dispatch_id
    )
    if not deleted:
        return DischargeResult(
            discharged=False,
            branch=branch_name,
            verb=verb,
            tip_sha=tip_sha,
            archive_tag=archive_tag,
            refused_reason=error,
            worktree_release=worktree_release,
        )

    debt = get_branch_debt(branch_name=branch_name)
    discharge_branch_debt(branch_name=branch_name, verb=verb, note=note)
    remove_land_required_tag(thread_id=debt.thread_id if debt is not None else None)
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
        archive_skipped_reason=archive_skipped_reason,
        worktree_release=worktree_release,
    )


def _clear_disposition(branch_name: str) -> None:
    from services.git_integration_worker.cursor_sdk_lane_b_disposition import (
        clear_disposition,
    )

    clear_disposition(branch_name=branch_name)


def discharge_landed(
    *,
    repo: Path,
    branch_name: str,
    completing_dispatch_id: str | None = None,
) -> DischargeResult:
    """Retire a branch whose work is verifiably on master."""
    retired = _already_retired(repo=repo, branch_name=branch_name)
    if retired is not None:
        return retired
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
    patch_note = probe.patch_id_note
    result = _finish(
        repo=repo,
        branch_name=branch_name,
        verb=DISCHARGE_LANDED,
        note=patch_note,
        completing_dispatch_id=completing_dispatch_id,
    )
    return DischargeResult(
        discharged=result.discharged,
        branch=result.branch,
        verb=result.verb,
        tip_sha=result.tip_sha,
        archive_tag=result.archive_tag,
        archive_skipped_reason=result.archive_skipped_reason,
        refused_reason=result.refused_reason,
        probe=probe,
        inherited=result.inherited,
        worktree_release=result.worktree_release,
    )


def discharge_unlanded(
    *,
    repo: Path,
    branch_name: str,
    declared_tip_sha: str,
    completing_dispatch_id: str | None = None,
) -> DischargeResult:
    """Archive a lane branch left intentionally off master; tip sha must match."""
    from services.git_integration_worker.cursor_sdk_hub_land_scope import (
        commit_reachable_on_branch_ref,
    )

    declared = (declared_tip_sha or "").strip().lower()
    if not declared:
        return DischargeResult(
            discharged=False,
            branch=branch_name,
            verb=DISCHARGE_UNLANDED,
            refused_reason="unlanded requires a tip sha",
        )
    if not commit_reachable_on_branch_ref(repo, branch_name=branch_name, sha=declared):
        return DischargeResult(
            discharged=False,
            branch=branch_name,
            verb=DISCHARGE_UNLANDED,
            refused_reason=f"tip {declared} not reachable on {branch_name}",
        )
    rev = _git(repo.resolve(), "rev-parse", "--verify", f"{branch_name}^{{commit}}")
    actual_tip = rev.stdout.strip().lower() if rev.returncode == 0 else None
    if actual_tip and actual_tip != declared:
        return DischargeResult(
            discharged=False,
            branch=branch_name,
            verb=DISCHARGE_UNLANDED,
            refused_reason=(
                f"declared tip {declared} does not match branch tip {actual_tip}"
            ),
        )
    return _finish(
        repo=repo,
        branch_name=branch_name,
        verb=DISCHARGE_UNLANDED,
        note=f"unlanded {declared}",
        completing_dispatch_id=completing_dispatch_id,
    )


def discharge_discard(
    *,
    repo: Path,
    branch_name: str,
    reason: str,
    completing_dispatch_id: str | None = None,
) -> DischargeResult:
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
        completing_dispatch_id=completing_dispatch_id,
    )


def resolve_completing_dispatch_id(explicit: str | None) -> str | None:
    """Body field, else the harness env the bridge stamps on the dispatch.

    GIW's own process does not inherit a nested dispatch's env. A dispatch
    that POSTs must send ``completing_dispatch_id``, or be the process whose
    environment already carries ``CURSOR_SDK_DISPATCH_ID``.
    """
    body = (explicit or "").strip()
    if body:
        return body
    from services.git_integration_worker.cursor_sdk_context import (
        CURSOR_SDK_DISPATCH_ID_ENV,
    )

    stamped = os.environ.get(CURSOR_SDK_DISPATCH_ID_ENV, "").strip()
    return stamped or None


def discharge(
    *,
    repo: Path,
    branch_name: str,
    verb: str,
    reason: str | None = None,
    declared_tip_sha: str | None = None,
    completing_dispatch_id: str | None = None,
) -> DischargeResult:
    """Dispatch on the declared verb — the single entry point a closeout names."""
    normalized = (verb or "").strip().lower()
    if normalized == DISCHARGE_LANDED:
        return discharge_landed(
            repo=repo,
            branch_name=branch_name,
            completing_dispatch_id=completing_dispatch_id,
        )
    if normalized == DISCHARGE_DISCARD:
        return discharge_discard(
            repo=repo,
            branch_name=branch_name,
            reason=reason or "",
            completing_dispatch_id=completing_dispatch_id,
        )
    if normalized == DISCHARGE_UNLANDED:
        return discharge_unlanded(
            repo=repo,
            branch_name=branch_name,
            declared_tip_sha=declared_tip_sha or "",
            completing_dispatch_id=completing_dispatch_id,
        )
    return DischargeResult(
        discharged=False,
        branch=branch_name,
        verb=normalized or "(unset)",
        refused_reason=(
            f"unknown land_disposition {normalized!r} — "
            f"expected {DISCHARGE_LANDED!r}, {DISCHARGE_DISCARD!r}, "
            f"or {DISCHARGE_UNLANDED!r} <sha>"
        ),
    )
