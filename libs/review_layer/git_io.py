"""Read-only git subprocess helpers for review-layer export."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

_GIT_TIMEOUT_S = 30


class ReviewTargetUnreachable(Exception):  # noqa: N818 — stable API name in AC
    """Raised when a review head cannot be materialized from hub git."""

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


def validate_git_ref(ref: str) -> None:
    """Reject ref strings that could be parsed as git options."""
    if ref.startswith("-"):
        raise ReviewTargetUnreachable("invalid git ref")


def git_run_bytes(repo: Path, *args: str) -> bytes:
    """Run a git subprocess and return raw stdout bytes (no decode/strip)."""
    try:
        proc = subprocess.run(
            ["git", "-C", str(repo), *args],
            capture_output=True,
            timeout=_GIT_TIMEOUT_S,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise ReviewTargetUnreachable("git command timed out") from exc
    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or b"git failed")[:240]
        try:
            reason = err.decode("utf-8", errors="replace").strip()
        except Exception:
            reason = "git failed"
        raise ReviewTargetUnreachable(reason)
    return proc.stdout


def resolve_commit(repo: Path, ref: str) -> str:
    """Resolve a ref to a 40-char commit sha."""
    validate_git_ref(ref)
    out = git_run_bytes(repo, "rev-parse", "--verify", f"{ref}^{{commit}}")
    sha = out.decode("ascii", errors="strict").strip()
    if not re.fullmatch(r"[0-9a-f]{40}", sha):
        raise ReviewTargetUnreachable("invalid commit sha")
    return sha


def merge_base(repo: Path, *refs: str) -> str:
    """Return merge-base sha for two or more commits."""
    out = git_run_bytes(repo, "merge-base", *refs)
    sha = out.decode("ascii", errors="strict").strip()
    if not re.fullmatch(r"[0-9a-f]{40}", sha):
        raise ReviewTargetUnreachable("merge-base failed")
    return sha


def default_diff_base(repo: Path, head_sha: str) -> str:
    """Merge-base of head against master or main."""
    for base_ref in ("master", "main"):
        try:
            base_tip = resolve_commit(repo, base_ref)
        except ReviewTargetUnreachable:
            continue
        try:
            return merge_base(repo, base_tip, head_sha)
        except ReviewTargetUnreachable:
            continue
    raise ReviewTargetUnreachable("no merge-base against master/main")


def diff_base_sha(repo: Path, head_sha: str, base_ref: str | None) -> str:
    """Three-dot semantics: merge-base between optional base ref and head."""
    if base_ref:
        base_tip = resolve_commit(repo, base_ref)
        return merge_base(repo, base_tip, head_sha)
    return default_diff_base(repo, head_sha)


def is_ancestor(repo: Path, descendant: str, ancestor: str) -> bool:
    """Return whether ``descendant`` is an ancestor of ``ancestor``."""
    try:
        proc = subprocess.run(
            [
                "git",
                "-C",
                str(repo),
                "merge-base",
                "--is-ancestor",
                descendant,
                ancestor,
            ],
            capture_output=True,
            timeout=_GIT_TIMEOUT_S,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise ReviewTargetUnreachable("git command timed out") from exc
    if proc.returncode == 0:
        return True
    if proc.returncode == 1:
        return False
    err = (proc.stderr or proc.stdout or b"ancestor check failed")[:240]
    raise ReviewTargetUnreachable(err.decode("utf-8", errors="replace").strip())


def parse_name_status_z(raw: bytes) -> list[str]:
    """Parse ``git diff --name-status -z --no-renames`` into repo-relative paths."""
    paths: list[str] = []
    chunks = raw.split(b"\0")
    idx = 0
    while idx < len(chunks):
        status_blob = chunks[idx]
        idx += 1
        if not status_blob:
            continue
        if idx >= len(chunks):
            break
        rel_blob = chunks[idx]
        idx += 1
        if not rel_blob:
            continue
        status = status_blob.decode("ascii", errors="replace")
        if status.startswith("D"):
            continue
        rel = rel_blob.decode("utf-8", errors="surrogateescape")
        if rel:
            paths.append(rel)
    return paths


def cat_file_batch(repo: Path, head_sha: str, paths: list[str]) -> dict[str, bytes]:
    """Fetch blob bytes for ``paths`` at ``head_sha`` via one ``cat-file --batch``."""
    if not paths:
        return {}
    requests = "".join(f"{head_sha}:{p}\n" for p in paths).encode("ascii")
    try:
        proc = subprocess.run(
            ["git", "-C", str(repo), "cat-file", "--batch"],
            input=requests,
            capture_output=True,
            timeout=_GIT_TIMEOUT_S,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise ReviewTargetUnreachable("git command timed out") from exc
    if proc.returncode != 0:
        raise ReviewTargetUnreachable("cat-file batch failed")
    return _parse_cat_file_batch(proc.stdout, paths)


def _parse_cat_file_batch(stdout: bytes, paths: list[str]) -> dict[str, bytes]:
    out: dict[str, bytes] = {}
    idx = 0
    data = stdout
    for path in paths:
        nl = data.find(b"\n", idx)
        if nl < 0:
            break
        header = data[idx:nl].decode("ascii", errors="replace")
        idx = nl + 1
        parts = header.split()
        if len(parts) < 3 or parts[1] != "blob":
            continue
        try:
            size = int(parts[2])
        except ValueError:
            continue
        blob = data[idx : idx + size]
        idx += size
        if idx < len(data) and data[idx : idx + 1] == b"\n":
            idx += 1
        out[path] = blob
    return out
