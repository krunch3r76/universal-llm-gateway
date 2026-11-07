"""Read-only git subprocess helpers for review-target resolution."""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

_GIT_TIMEOUT_S = 30
_REF_MISS_MARKERS = (
    "unknown revision",
    "bad revision",
    "ambiguous argument",
    "needed a single revision",
)


class ReviewTargetUnreachable(Exception):  # noqa: N818
    """Named ref does not resolve or is not reachable on the lane."""

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


class ReviewLayerInfraError(Exception):
    """Hub git, timeout, or environment failure."""

    def __init__(self, reason: str = "git unavailable") -> None:
        self.reason = reason
        super().__init__(reason)


def hub_repo_path() -> Path:
    """Resolve the hub git checkout used for read-only review resolution."""
    raw = os.environ.get("GIT_INTEGRATION_SOURCE_REPO", "").strip()
    if raw:
        return Path(raw).expanduser().resolve()
    from implement_admission.closeout_helpers import workspaces_root

    return workspaces_root()


def validate_git_ref(ref: str) -> None:
    """Reject ref strings that could be parsed as git options."""
    if ref.startswith("-"):
        raise ReviewTargetUnreachable("invalid git ref")


def _git_env() -> dict[str, str]:
    env = os.environ.copy()
    env["LC_ALL"] = "C"
    return env


def _git_run_bytes(repo: Path, *args: str) -> bytes:
    try:
        proc = subprocess.run(
            ["git", "-C", str(repo), *args],
            capture_output=True,
            timeout=_GIT_TIMEOUT_S,
            check=False,
            env=_git_env(),
        )
    except FileNotFoundError as exc:
        raise ReviewLayerInfraError("git executable missing") from exc
    except subprocess.TimeoutExpired as exc:
        raise ReviewLayerInfraError("git command timed out") from exc
    if proc.returncode != 0:
        if (
            args
            and args[0] == "merge-base"
            and proc.returncode == 1
            and not (proc.stdout or proc.stderr or b"").strip()
        ):
            raise ReviewTargetUnreachable("no merge-base")
        err = (proc.stderr or proc.stdout or b"git failed").decode(
            "utf-8", errors="replace"
        ).lower()
        if any(marker in err for marker in _REF_MISS_MARKERS):
            raise ReviewTargetUnreachable("ref does not resolve")
        raise ReviewLayerInfraError("git command failed")
    return proc.stdout


def resolve_commit(repo: Path, ref: str) -> str:
    """Resolve a ref to a 40-char commit sha."""
    validate_git_ref(ref)
    if not (repo / ".git").exists():
        raise ReviewLayerInfraError("hub path is not a git repository")
    out = _git_run_bytes(repo, "rev-parse", "--verify", f"{ref}^{{commit}}")
    sha = out.decode("ascii", errors="strict").strip()
    if not re.fullmatch(r"[0-9a-f]{40}", sha):
        raise ReviewTargetUnreachable("invalid commit sha")
    return sha


def merge_base(repo: Path, *refs: str) -> str:
    out = _git_run_bytes(repo, "merge-base", *refs)
    sha = out.decode("ascii", errors="strict").strip()
    if not re.fullmatch(r"[0-9a-f]{40}", sha):
        raise ReviewTargetUnreachable("merge-base failed")
    return sha


def is_ancestor_of(repo: Path, ancestor: str, descendant: str) -> bool:
    """True when ``ancestor`` is reachable from ``descendant`` (``git merge-base --is-ancestor``)."""
    if ancestor == descendant:
        return True
    try:
        proc = subprocess.run(
            [
                "git",
                "-C",
                str(repo),
                "merge-base",
                "--is-ancestor",
                ancestor,
                descendant,
            ],
            capture_output=True,
            timeout=_GIT_TIMEOUT_S,
            check=False,
            env=_git_env(),
        )
    except FileNotFoundError as exc:
        raise ReviewLayerInfraError("git executable missing") from exc
    except subprocess.TimeoutExpired as exc:
        raise ReviewLayerInfraError("git command timed out") from exc
    if proc.returncode == 0:
        return True
    if proc.returncode == 1:
        return False
    raise ReviewLayerInfraError("ancestor check failed")


def diff_name_only_z(repo: Path, base_sha: str, head_sha: str) -> tuple[str, ...]:
    """Return changed paths between ``base_sha`` and ``head_sha`` (no deleted dirs)."""
    raw = _git_run_bytes(
        repo,
        "diff",
        "--name-only",
        "-z",
        "--no-renames",
        "--diff-filter=d",
        f"{base_sha}..{head_sha}",
    )
    paths: list[str] = []
    for chunk in raw.split(b"\0"):
        if chunk:
            paths.append(chunk.decode("utf-8", errors="surrogateescape"))
    return tuple(paths)
