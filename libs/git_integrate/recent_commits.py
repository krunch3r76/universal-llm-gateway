"""Oneline git history for catch-up — subjects only, never diffs.

Shared by MCP ``fs(op=recent_commits)`` and GIW L2 hop orientation. Git is a
checkpoint layer, not a project index (``git-posture``); this query exists so
life/CDP seats can see *what landed* without commissioning Auto for ``git log``.
"""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path
from typing import Any

DEFAULT_N = 15
HOP_N = 8
MAX_N = 20
QUERY_PATH = "universal-llm-gateway"
_SHA_RE = re.compile(r"^[0-9a-fA-F]{7,40}$")
_LOG_FORMAT = "%H%x09%s%x09%an%x09%aI"
_GIT_TIMEOUT_S = 10


def clamp_n(n: int) -> int:
    """Clamp a requested count into ``[1, MAX_N]``."""
    return min(max(int(n), 1), MAX_N)


def log_oneline(
    repo: Path,
    *,
    since: str | None = None,
    n: int = DEFAULT_N,
) -> dict[str, Any]:
    """Return oneline commits for *repo* (newest first / oldest last).

    Response keys: ``head``, ``commits`` (``sha``, ``subject``, ``author``,
    ``authored_at``), ``since`` (SHA bound or ``last N``), ``truncated``.
    Never includes diffs, patches, or path lists.
    """
    n = clamp_n(n)
    since_bound = (since or "").strip() or None
    if since_bound is not None and _SHA_RE.fullmatch(since_bound) is None:
        raise ValueError(f"since must be a git SHA (7-40 hex), got {since_bound!r}")

    head = _rev_parse(repo)
    resolved_since: str = since_bound if since_bound is not None else f"last {n}"
    empty: dict[str, Any] = {
        "head": head or "",
        "commits": [],
        "since": resolved_since,
        "truncated": False,
    }
    if not head:
        return empty

    spec = [f"{since_bound}..HEAD"] if since_bound else []
    try:
        proc = subprocess.run(
            [
                "git",
                "-C",
                str(repo),
                "log",
                *spec,
                f"--format={_LOG_FORMAT}",
                f"--max-count={n + 1}",
            ],
            capture_output=True,
            check=True,
            timeout=_GIT_TIMEOUT_S,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError):
        return empty

    rows = [
        line.decode("utf-8", errors="replace") if isinstance(line, bytes) else line
        for line in proc.stdout.splitlines()
        if line
    ]
    truncated = len(rows) > n
    commits = [_parse_log_row(row) for row in rows[:n]]
    return {
        "head": head,
        "commits": [c for c in commits if c is not None],
        "since": resolved_since,
        "truncated": truncated,
    }


def format_hop_slice(
    result: dict[str, Any],
    *,
    include_body: bool = True,
    query_path: str = QUERY_PATH,
) -> str:
    """Render the L2 hop ``recent_commits`` section (query pointer always kept)."""
    head = str(result.get("head") or "")
    if not head:
        return format_hop_unavailable(query_path=query_path)
    commits = result.get("commits") or []
    n = len(commits) if isinstance(commits, list) else 0
    header = f"recent_commits: HEAD={head} n={n}"
    query = (
        f'  query: fs(op="recent_commits", sandbox="workspaces", '
        f'path="{query_path}", since="{head}")'
    )
    if not include_body:
        return "\n".join(
            [f"{header} (body dropped for screen budget)", query]
        )
    lines = [header]
    for commit in commits:
        sha = str(commit.get("sha", ""))
        subject = str(commit.get("subject", "")).replace("\n", " ")
        lines.append(f"  {sha[:7]} {subject}")
    lines.append(query)
    return "\n".join(lines)


def format_hop_unavailable(*, query_path: str = QUERY_PATH) -> str:
    """Fail-soft hop slice: keep the on-demand query, drop live subjects."""
    return (
        "recent_commits: unavailable\n"
        f'  query: fs(op="recent_commits", sandbox="workspaces", '
        f'path="{query_path}")'
    )


def source_repo_path() -> Path:
    """GIW live checkout; same default as worker config."""
    return Path(
        os.environ.get(
            "GIT_INTEGRATION_SOURCE_REPO",
            "/mnt/torus/projects/universal-llm-gateway",
        )
    )


def _rev_parse(repo: Path) -> str:
    try:
        proc = subprocess.run(
            ["git", "-C", str(repo), "rev-parse", "HEAD"],
            capture_output=True,
            check=True,
            timeout=_GIT_TIMEOUT_S,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError):
        return ""
    sha = proc.stdout.decode("utf-8", errors="replace").strip()
    return sha


def _parse_log_row(row: str) -> dict[str, str] | None:
    parts = row.split("\t", 3)
    if len(parts) != 4:
        return None
    sha, subject, author, authored_at = parts
    return {
        "sha": sha,
        "subject": subject,
        "author": author,
        "authored_at": authored_at,
    }
