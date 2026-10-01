"""Lane-B commit-on-terminal, salvage, and branch state (S3)."""

from __future__ import annotations

import os
import re
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

from universal_logging import get_logger

from services.git_integration_worker.cursor_home import dispatch_git_env_vars

logger = get_logger(__name__)

_GIT_TIMEOUT_S = 60.0
_ERROR_LIMIT = 500
_CURSOR_SKILLS_PREFIX = ".cursor/skills/"
_FILES_EXPECTED_LINE_RE = re.compile(r"(?i)^files_expected:\s*(.*)$")
_TOP_LEVEL_FIELD_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]*:\s")
_SKILL_SCOPE_TOKEN_RE = re.compile(r"\.cursor/skills(?:/[\w./-]*)?")


@dataclass(frozen=True, slots=True)
class BranchState:
    """Tip and merge posture for a dispatch branch keyed at mint."""

    head_sha: str | None
    commits_ahead: int | None
    merged_into_master: bool
    content_landed: bool = False

    @property
    def is_empty(self) -> bool:
        """True only for a measured zero — unknown tip is not empty."""
        return self.commits_ahead == 0

    @property
    def safe_to_delete(self) -> bool:
        """Deletable only when it carries no work, or its work reached master."""
        return self.is_empty or self.merged_into_master or self.content_landed


@dataclass(frozen=True, slots=True)
class SalvageResult:
    """Outcome of a salvage or terminal commit attempt."""

    committed: bool
    head_sha: str | None
    commit_sha: str | None = None
    refused: bool = False
    error: str | None = None

    @property
    def short_error(self) -> str:
        """Single-line proximate cause, safe to embed in a deviation token."""
        lines = [ln.strip() for ln in (self.error or "").splitlines() if ln.strip()]
        return (lines[-1] if lines else "unknown")[:120]


def is_worktree_dirty(worktree_path: Path) -> bool:
    """True when the worktree has any porcelain delta vs HEAD."""
    proc = subprocess.run(
        ["git", "-C", str(worktree_path.resolve()), "status", "--porcelain"],
        capture_output=True,
        text=True,
        timeout=_GIT_TIMEOUT_S,
        check=False,
    )
    if proc.returncode != 0:
        return False
    return bool(proc.stdout.strip())


def _truncate(text: str, *, limit: int = _ERROR_LIMIT) -> str:
    """Keep the tail of *text*; git and hook failures name the cause last."""
    collapsed = text.strip()
    if len(collapsed) <= limit:
        return collapsed
    return "…" + collapsed[-limit:]


def _show_toplevel(repo_or_wt: Path) -> Path | None:
    proc = subprocess.run(
        ["git", "-C", str(repo_or_wt.resolve()), "rev-parse", "--show-toplevel"],
        capture_output=True,
        text=True,
        timeout=_GIT_TIMEOUT_S,
        check=False,
    )
    if proc.returncode != 0:
        return None
    raw = proc.stdout.strip()
    return Path(raw).resolve() if raw else None


def _abbrev_ref(repo_or_wt: Path) -> str | None:
    proc = subprocess.run(
        ["git", "-C", str(repo_or_wt.resolve()), "rev-parse", "--abbrev-ref", "HEAD"],
        capture_output=True,
        text=True,
        timeout=_GIT_TIMEOUT_S,
        check=False,
    )
    if proc.returncode != 0:
        return None
    branch = proc.stdout.strip()
    if not branch or branch == "HEAD":
        return None
    return branch


def _emit_capture_refused(
    *,
    dispatch_id: str,
    worktree_path: Path,
    branch_name: str | None,
) -> None:
    from services.git_integration_worker.cursor_sdk_events import (
        emit_sdk_lane_capture_refused,
    )

    try:
        emit_sdk_lane_capture_refused(
            dispatch_id=dispatch_id,
            worktree_path=str(worktree_path.resolve()),
            branch_name=branch_name or "",
        )
    except Exception:
        pass


def _salvage_identity_refusal(
    wt: Path,
    *,
    dispatch_id: str,
    branch_name: str | None,
) -> str | None:
    """Return a refusal token when a registry row exists and identity checks fail."""
    from services.git_integration_worker.cursor_sdk_worktree import (
        lookup_dispatch_worktree,
    )

    record = lookup_dispatch_worktree(dispatch_id=dispatch_id)
    if record is None:
        return None

    wt_res = wt.resolve()
    record_wt = record.worktree_path.resolve()
    if wt_res != record_wt:
        return "worktree_path_mismatch"
    if branch_name is not None and branch_name != record.branch_name:
        return "branch_name_mismatch"
    toplevel = _show_toplevel(wt)
    if toplevel is None or toplevel != wt_res:
        return "toplevel_mismatch"
    if record.source_repo:
        hub = Path(record.source_repo).expanduser().resolve()
        if wt_res == hub:
            return "hub_worktree"
    head_branch = _abbrev_ref(wt)
    if head_branch != record.branch_name:
        return "head_branch_mismatch"
    return None


def _rev_parse(repo_or_wt: Path, ref: str) -> str | None:
    proc = subprocess.run(
        ["git", "-C", str(repo_or_wt.resolve()), "rev-parse", ref],
        capture_output=True,
        text=True,
        timeout=_GIT_TIMEOUT_S,
        check=False,
    )
    if proc.returncode != 0:
        return None
    sha = proc.stdout.strip()
    return sha or None


def _files_expected_field_text(prose: str) -> str:
    """Return the ``files_expected:`` field body, bullets included."""
    lines = prose.splitlines()
    block: list[str] = []
    in_field = False
    for line in lines:
        stripped = line.strip()
        if not in_field:
            match = _FILES_EXPECTED_LINE_RE.match(stripped)
            if not match:
                continue
            in_field = True
            inline = match.group(1).strip()
            if inline:
                block.append(inline)
            continue
        if not stripped:
            continue
        if _TOP_LEVEL_FIELD_RE.match(stripped):
            break
        block.append(stripped)
    return "\n".join(block)


def _normalize_repo_rel(rel_path: str) -> str:
    """Drop a literal ``./`` prefix and leading slashes.

    ``str.lstrip("./")`` is a character set. It also deletes the leading dot
    of ``.cursor/skills/...``, so the prefix check never matches.
    """
    norm = rel_path.replace("\\", "/").strip()
    while norm.startswith("./"):
        norm = norm[2:]
    return norm.lstrip("/")


def _is_cursor_skills_path(rel_path: str) -> bool:
    norm = _normalize_repo_rel(rel_path)
    return norm == ".cursor/skills" or norm.startswith(_CURSOR_SKILLS_PREFIX)


def packet_scopes_cursor_skill_path(packet_text: str | None, rel_path: str) -> bool:
    """True when ``files_expected:`` names *rel_path* or a directory prefix of it.

    Body prose outside that field does not scope a skill path in. A token of
    ``.cursor/skills`` with no child scopes the whole tree.
    """
    if not packet_text or not _is_cursor_skills_path(rel_path):
        return False
    field = _files_expected_field_text(packet_text)
    if not field:
        return False
    norm = _normalize_repo_rel(rel_path)
    for match in _SKILL_SCOPE_TOKEN_RE.finditer(field):
        token = match.group(0).rstrip("/")
        if token == ".cursor/skills":
            return True
        if norm == token or norm.startswith(token + "/"):
            return True
    return False


def _drop_unscoped_cursor_skills(wt: Path, packet_text: str | None) -> str | None:
    """Unstage ``.cursor/skills/`` paths the packet does not scope in.

    Returns an error string when git fails, else None. Paths the packet
    names in ``files_expected:`` stay staged.
    """
    listed = subprocess.run(
        ["git", "-C", str(wt), "diff", "--cached", "--name-only", "-z"],
        capture_output=True,
        text=True,
        timeout=_GIT_TIMEOUT_S,
        check=False,
    )
    if listed.returncode != 0:
        return _truncate(listed.stderr.strip() or "diff --cached failed")
    drop = [
        name
        for name in listed.stdout.split("\0")
        if name
        and _is_cursor_skills_path(name)
        and not packet_scopes_cursor_skill_path(packet_text, name)
    ]
    if not drop:
        return None
    reset = subprocess.run(
        ["git", "-C", str(wt), "reset", "-q", "--", *drop],
        capture_output=True,
        text=True,
        timeout=_GIT_TIMEOUT_S,
        check=False,
    )
    if reset.returncode != 0:
        return _truncate(reset.stderr.strip() or "reset unscoped skills failed")
    return None


def salvage_commit(
    worktree_path: Path,
    *,
    message: str,
    dispatch_id: str,
    thread_id: str | None = None,
    packet_text: str | None = None,
    respect_skill_scope: bool = False,
    branch_name: str | None = None,
) -> SalvageResult:
    """Commit all dirty paths in the worktree; no-op when clean.

    A clean tree and a git-refused commit both yield ``committed=False``; callers
    that may destroy the worktree must branch on ``refused``, which is set only
    when work exists and git declined to record it (for example a failing
    pre-commit hook or the GIW subtree F821 land gate).
    """
    wt = worktree_path.resolve()
    head = _rev_parse(wt, "HEAD")
    if not is_worktree_dirty(wt):
        return SalvageResult(committed=False, head_sha=head)

    identity_reason = _salvage_identity_refusal(
        wt, dispatch_id=dispatch_id, branch_name=branch_name
    )
    if identity_reason is not None:
        _emit_capture_refused(
            dispatch_id=dispatch_id,
            worktree_path=wt,
            branch_name=branch_name,
        )
        logger.error(
            "lane_b salvage identity refused path=%s dispatch_id=%s reason=%s",
            wt,
            dispatch_id,
            identity_reason,
        )
        return SalvageResult(
            committed=False,
            head_sha=head,
            refused=True,
            error=_truncate(f"lane_b_identity: {identity_reason}"),
        )

    from services.git_integration_worker.giw_f821_gate import run_giw_subtree_f821_check

    f821 = run_giw_subtree_f821_check(wt)
    if not f821.passed:
        detail = (f821.stderr or f821.stdout or "F821 check failed").strip()
        logger.error(
            "lane_b salvage commit refused path=%s f821_exit=%s err=%s",
            wt,
            f821.exit_code,
            detail,
        )
        return SalvageResult(
            committed=False,
            head_sha=head,
            refused=True,
            error=_truncate(f"giw_f821_gate: {detail}"),
        )

    add = subprocess.run(
        ["git", "-C", str(wt), "add", "-A"],
        capture_output=True,
        text=True,
        timeout=_GIT_TIMEOUT_S,
        check=False,
    )
    if add.returncode != 0:
        err = add.stderr.strip()
        logger.error("lane_b salvage add refused path=%s err=%s", wt, err)
        return SalvageResult(
            committed=False,
            head_sha=head,
            refused=True,
            error=_truncate(err),
        )

    if respect_skill_scope:
        dropped = _drop_unscoped_cursor_skills(wt, packet_text)
        if dropped:
            logger.error(
                "lane_b salvage skill unstage refused path=%s err=%s",
                wt,
                dropped,
            )
            return SalvageResult(
                committed=False,
                head_sha=head,
                refused=True,
                error=dropped,
            )

    git_env = {**os.environ, **dispatch_git_env_vars(dispatch_id, thread_id=thread_id)}
    commit = subprocess.run(
        ["git", "-C", str(wt), "commit", "-m", message],
        capture_output=True,
        text=True,
        timeout=_GIT_TIMEOUT_S,
        check=False,
        env=git_env,
    )
    if commit.returncode != 0:
        err = commit.stderr.strip() or commit.stdout.strip()
        if "nothing to commit" in err.lower():
            return SalvageResult(committed=False, head_sha=_rev_parse(wt, "HEAD"))
        logger.error("lane_b salvage commit refused path=%s err=%s", wt, err)
        return SalvageResult(
            committed=False,
            head_sha=_rev_parse(wt, "HEAD"),
            refused=True,
            error=_truncate(err),
        )

    commit_sha = _rev_parse(wt, "HEAD")
    return SalvageResult(committed=True, head_sha=commit_sha, commit_sha=commit_sha)


def commit_on_terminal(
    *,
    dispatch_id: str,
    worktree_path: Path,
    branch_name: str,
    packet_text: str | None = None,
) -> SalvageResult:
    """Durability commit at terminal after porcelain capture (Lane-B only).

    ``.cursor/skills/`` stays unstaged unless ``files_expected:`` in
    *packet_text* scopes that path in. Mint-time skill-tree copies are not
    packet work (friction a:36881).
    """
    message = f"cursor-sdk: lane-b terminal {dispatch_id}"
    return salvage_commit(
        worktree_path,
        message=message,
        dispatch_id=dispatch_id,
        packet_text=packet_text,
        respect_skill_scope=True,
        branch_name=branch_name,
    )


def branch_state(
    source_repo: Path,
    *,
    branch_name: str,
    branch_point: str,
) -> BranchState:
    """Resolve branch tip, commits since mint point, and merge into master."""
    repo = source_repo.resolve()
    head_sha = _rev_parse(repo, branch_name)
    if head_sha is None:
        # Tip unresolved — meter unknown. Never launder into measured 0.
        return BranchState(head_sha=None, commits_ahead=None, merged_into_master=False)

    count_proc = subprocess.run(
        [
            "git",
            "-C",
            str(repo),
            "rev-list",
            "--count",
            f"{branch_point}..{branch_name}",
        ],
        capture_output=True,
        text=True,
        timeout=_GIT_TIMEOUT_S,
        check=False,
    )
    commits_ahead = 0
    if count_proc.returncode == 0 and count_proc.stdout.strip().isdigit():
        commits_ahead = int(count_proc.stdout.strip())

    merged_proc = subprocess.run(
        ["git", "-C", str(repo), "branch", "--merged", "master"],
        capture_output=True,
        text=True,
        timeout=_GIT_TIMEOUT_S,
        check=False,
    )
    merged_names: set[str] = set()
    if merged_proc.returncode == 0:
        for line in merged_proc.stdout.splitlines():
            name = normalize_git_branch_list_name(line)
            if name:
                merged_names.add(name)

    # A branch with no commits of its own is an ancestor of master and therefore
    # appears in ``--merged``; that is "never diverged", not "work reached master".
    merged_into_master = commits_ahead > 0 and branch_name in merged_names

    content_landed = False
    if commits_ahead > 0 and not merged_into_master:
        content_landed = _patches_present_in_master(repo, branch_name=branch_name)

    return BranchState(
        head_sha=head_sha,
        commits_ahead=commits_ahead,
        merged_into_master=merged_into_master,
        content_landed=content_landed,
    )


def normalize_git_branch_list_name(line: str) -> str:
    """Strip ``git branch`` current/worktree markers from a list line.

    Porcelain prefixes: ``* `` (checked out here), ``+ `` (other worktree),
    or two spaces. ``lstrip("* ")`` alone drops ``*`` but leaves ``+``, which
    made worktree-checked-out ``cursor-sdk/*`` heads invisible to the meter.
    """
    name = line.strip()
    while name[:1] in "*+":
        name = name[1:].lstrip()
    return name.strip()


def merge_base_with_master(source_repo: Path, *, branch_name: str) -> str | None:
    """Return ``git merge-base master <branch>`` or ``None`` on failure."""
    proc = subprocess.run(
        ["git", "-C", str(source_repo.resolve()), "merge-base", "master", branch_name],
        capture_output=True,
        text=True,
        timeout=_GIT_TIMEOUT_S,
        check=False,
    )
    if proc.returncode != 0:
        return None
    sha = proc.stdout.strip()
    return sha or None


def list_cursor_sdk_branches(source_repo: Path) -> list[str]:
    """List local ``cursor-sdk/*`` branch names."""
    proc = subprocess.run(
        ["git", "-C", str(source_repo.resolve()), "branch", "--list", "cursor-sdk/*"],
        capture_output=True,
        text=True,
        timeout=_GIT_TIMEOUT_S,
        check=False,
    )
    if proc.returncode != 0:
        return []
    names: list[str] = []
    for line in proc.stdout.splitlines():
        name = normalize_git_branch_list_name(line)
        if name.startswith("cursor-sdk/"):
            names.append(name)
    return names


def orphan_branch_state(source_repo: Path, *, branch_name: str) -> BranchState:
    """Evaluate branch safety for an unregistered orphan via merge-base."""
    repo = source_repo.resolve()
    head_sha = _rev_parse(repo, branch_name)
    if head_sha is None:
        return BranchState(head_sha=None, commits_ahead=None, merged_into_master=False)
    branch_point = merge_base_with_master(repo, branch_name=branch_name)
    if branch_point is None:
        return BranchState(
            head_sha=head_sha,
            commits_ahead=1,
            merged_into_master=False,
            content_landed=False,
        )
    return branch_state(
        repo,
        branch_name=branch_name,
        branch_point=branch_point,
    )


def branch_tip_age_s(source_repo: Path, *, branch_name: str) -> float | None:
    """Seconds since the tip commit on *branch_name*, or ``None`` when unknown."""
    proc = subprocess.run(
        [
            "git",
            "-C",
            str(source_repo.resolve()),
            "log",
            "-1",
            "--format=%ct",
            branch_name,
        ],
        capture_output=True,
        text=True,
        timeout=_GIT_TIMEOUT_S,
        check=False,
    )
    if proc.returncode != 0 or not proc.stdout.strip().isdigit():
        return None
    tip_ts = int(proc.stdout.strip())
    return max(0.0, time.time() - float(tip_ts))


def origin_lane_id_from_branch(branch_name: str) -> str | None:
    """Extract the lane id from ``cursor-sdk/lane-{thread}`` (or legacy suffix)."""
    lane_prefix = "cursor-sdk/lane-"
    if branch_name.startswith(lane_prefix):
        return branch_name[len(lane_prefix) :] or None
    prefix = "cursor-sdk/"
    if not branch_name.startswith(prefix):
        return None
    suffix = branch_name[len(prefix) :]
    return suffix or None


def origin_dispatch_id_from_branch(branch_name: str) -> str | None:
    """Legacy alias — lane id (or historic dispatch suffix) from a branch name."""
    return origin_lane_id_from_branch(branch_name)


def _patches_present_in_master(repo: Path, *, branch_name: str) -> bool:
    """True when every branch commit has a patch-equivalent already on master.

    Ancestry alone misses work that reached master by rebase or cherry-pick: the
    branch keeps a distinct SHA forever and would otherwise be retained for good.
    Any error, timeout, or unrecognised line means "not proven landed", so the
    caller retains the branch.
    """
    proc = subprocess.run(
        ["git", "-C", str(repo), "cherry", "master", branch_name],
        capture_output=True,
        text=True,
        timeout=_GIT_TIMEOUT_S,
        check=False,
    )
    if proc.returncode != 0:
        return False

    lines = [line.strip() for line in proc.stdout.splitlines() if line.strip()]
    if not lines:
        return False
    # "-" marks a commit whose patch is already upstream; "+" marks unique work.
    return all(line.startswith("- ") for line in lines)
