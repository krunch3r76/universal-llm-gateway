"""Recover a missing closeout capture head from on-disk refs.

Transport-terminated dispatches post a fenced error envelope instead of capture
JSON. Publishing ``capture head absent`` without probing refs under-claims work
that already exists and invites a seat to redo destructive preservation. Callers:
``compute_closeout_tree_state`` when parsed ``head_sha`` is missing.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from services.git_integration_worker.cursor_home import (
    DISPATCH_GIT_EMAIL_DOMAIN,
    dispatch_git_identity,
)

_GIT_TIMEOUT_S = 10.0
_REF_FORMAT = "%(objectname)%00%(refname:short)%00%(committername)%00%(committeremail)"
_REFLOG_FORMAT = "%H%x00%cn%x00%ce%x00%gd"


def recover_capture_head(
    source_repo: Path,
    *,
    dispatch_id: str,
) -> tuple[str | None, str | None]:
    """Return ``(head_sha, branch)`` for a ref this dispatch committed, else none.

    Matches committer name ``cursor-sdk/<dispatch_id>`` (and the thread-labeled
    name when that identity was used) or committer email
    ``<dispatch_id>@dispatch.git-integration-worker``. Prefers ``cursor-sdk/*``
    branch tips, then any heads tip, then a bounded reflog walk.
    """
    dispatch = (dispatch_id or "").strip()
    if not dispatch:
        return None, None
    name, email = dispatch_git_identity(dispatch)
    names = {name, f"cursor-sdk/{dispatch}"}
    emails = {email, f"{dispatch}@{DISPATCH_GIT_EMAIL_DOMAIN}"}
    for pattern in ("refs/heads/cursor-sdk/", "refs/heads/"):
        hit = _first_matching_ref(
            source_repo, pattern, names=names, emails=emails
        )
        if hit[0]:
            return hit
    return _first_matching_reflog(source_repo, names=names, emails=emails)


def _first_matching_ref(
    source_repo: Path,
    pattern: str,
    *,
    names: set[str],
    emails: set[str],
) -> tuple[str | None, str | None]:
    """Newest tip under *pattern* whose committer identity matches this dispatch."""
    lines = _git_lines(
        source_repo,
        "for-each-ref",
        "--sort=-committerdate",
        f"--format={_REF_FORMAT}",
        pattern,
    )
    for line in lines:
        parts = line.split("\0")
        if len(parts) != 4:
            continue
        sha, ref, committer_name, committer_email = parts
        if _identity_match(
            committer_name, committer_email, names=names, emails=emails
        ):
            return sha.strip(), ref.strip() or None
    return None, None


def _first_matching_reflog(
    source_repo: Path,
    *,
    names: set[str],
    emails: set[str],
) -> tuple[str | None, str | None]:
    """Bounded reflog walk — packet-specified committer probe when tips miss."""
    lines = _git_lines(
        source_repo,
        "log",
        "-g",
        "--walk-reflogs",
        "--all",
        f"--format={_REFLOG_FORMAT}",
        "-n",
        "200",
    )
    for line in lines:
        parts = line.split("\0")
        if len(parts) != 4:
            continue
        sha, committer_name, committer_email, selector = parts
        if not _identity_match(
            committer_name, committer_email, names=names, emails=emails
        ):
            continue
        branch = _branch_from_reflog_selector(selector)
        return sha.strip(), branch
    return None, None


def _identity_match(
    committer_name: str,
    committer_email: str,
    *,
    names: set[str],
    emails: set[str],
) -> bool:
    """True when committer name or email is this dispatch's git identity."""
    return committer_name.strip() in names or committer_email.strip() in emails


def _branch_from_reflog_selector(selector: str) -> str | None:
    """Parse ``refs/heads/foo@{n}`` / ``foo@{n}`` to a branch name."""
    text = (selector or "").strip()
    if not text:
        return None
    if "@{" in text:
        text = text[: text.index("@{")]
    if text.startswith("refs/heads/"):
        text = text[len("refs/heads/") :]
    if text in {"HEAD", ""}:
        return None
    return text


def _git_lines(source_repo: Path, *args: str) -> list[str]:
    """Return stdout lines from a non-raising git probe; empty on failure."""
    try:
        proc = subprocess.run(
            ["git", "-C", str(source_repo), *args],
            capture_output=True,
            text=True,
            timeout=_GIT_TIMEOUT_S,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    if proc.returncode != 0 or not proc.stdout:
        return []
    return [line for line in proc.stdout.splitlines() if line]


__all__ = ["recover_capture_head"]
