"""Ref-only master update-ref must not leave the hub index behind HEAD.

Friction a:39441. Dispatch f4de13d950f3 ran ``git update-ref refs/heads/master``
from a lane worktree. The hub stayed checked out on master, so the ref moved
and the index kept the pre-land blobs (staged ``M`` / ``D``). The bde16b19
checkout runs only inside ``integrate_retry_loop``, which that command never
entered. A later fast-forward that does not touch those paths leaves the
stale index in place.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.offline

_HOOKS_DIR = Path(__file__).resolve().parents[1] / "hooks"


def _git(
    repo: Path, *args: str, env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )


def _init_hub(tmp_path: Path) -> Path:
    repo = tmp_path / "hub"
    repo.mkdir()
    assert _git(repo, "init", "-b", "master").returncode == 0
    assert _git(repo, "config", "user.email", "t@example.com").returncode == 0
    assert _git(repo, "config", "user.name", "t").returncode == 0
    (repo / "README.md").write_text("base\n", encoding="utf-8")
    assert _git(repo, "add", "README.md").returncode == 0
    assert _git(repo, "commit", "-m", "base").returncode == 0
    assert _git(repo, "config", "core.hooksPath", str(_HOOKS_DIR)).returncode == 0
    return repo


def _commit_ahead(repo: Path) -> tuple[str, str]:
    """Return ``(old, new)`` with master still checked out at ``old``.

    The land commit lives on ``land`` so master never rewinds. A hard reset
    of master is a non-fast-forward and the forward-only hook refuses it.
    """
    old = _git(repo, "rev-parse", "HEAD").stdout.strip()
    assert _git(repo, "checkout", "-b", "land").returncode == 0
    (repo / "README.md").write_text("landed\n", encoding="utf-8")
    (repo / "added.txt").write_text("added\n", encoding="utf-8")
    assert _git(repo, "add", "README.md", "added.txt").returncode == 0
    assert _git(repo, "commit", "-m", "land").returncode == 0
    new = _git(repo, "rev-parse", "HEAD").stdout.strip()
    assert _git(repo, "checkout", "master").returncode == 0
    return old, new


def test_ref_only_update_ref_from_lane_repairs_hub_index(tmp_path: Path) -> None:
    """A lane ``update-ref`` of checked-out master must not leave staged M/D."""
    hub = _init_hub(tmp_path)
    old, new = _commit_ahead(hub)
    lane = tmp_path / "lane"
    assert _git(hub, "worktree", "add", "-b", "lane", str(lane), old).returncode == 0

    moved = _git(lane, "update-ref", "refs/heads/master", new, old)
    assert moved.returncode == 0, moved.stderr
    head = _git(hub, "rev-parse", "HEAD").stdout.strip()
    porcelain = _git(hub, "status", "--porcelain").stdout
    print(f"HUB_HEAD={head}")
    print(f"PORCELAIN_AFTER={porcelain!r}")
    assert head == new
    assert porcelain.strip() == ""
    assert (hub / "README.md").read_text(encoding="utf-8") == "landed\n"
    assert (hub / "added.txt").read_text(encoding="utf-8") == "added\n"
    cached_vs_old = _git(hub, "diff", "--cached", old, "--", "README.md", "added.txt")
    assert cached_vs_old.stdout.strip() != ""


def test_ref_only_update_ref_blocks_divergent_hub_bytes(tmp_path: Path) -> None:
    """Divergent hub bytes on a landed path refuse the ref move."""
    hub = _init_hub(tmp_path)
    old, new = _commit_ahead(hub)
    (hub / "README.md").write_text("operator wip\n", encoding="utf-8")
    before = (hub / "README.md").read_bytes()

    moved = _git(hub, "update-ref", "refs/heads/master", new, old)
    print(f"UPDATE_RC={moved.returncode}")
    print(f"UPDATE_STDERR={moved.stderr!r}")
    assert moved.returncode != 0
    assert _git(hub, "rev-parse", "refs/heads/master").stdout.strip() == old
    assert (hub / "README.md").read_bytes() == before


def test_ff_only_merge_on_hub_stays_clean_with_hook(tmp_path: Path) -> None:
    """The lease fast-forward path still updates the hub index and worktree."""
    hub = _init_hub(tmp_path)
    old, new = _commit_ahead(hub)
    merged = _git(hub, "merge", "--ff-only", new)
    assert merged.returncode == 0, merged.stderr
    assert "Updating" in merged.stdout
    assert _git(hub, "rev-parse", "HEAD").stdout.strip() == new
    assert _git(hub, "status", "--porcelain").stdout.strip() == ""
    assert (hub / "README.md").read_text(encoding="utf-8") == "landed\n"
    assert (hub / "added.txt").read_text(encoding="utf-8") == "added\n"


def test_hook_still_allows_commit_on_checked_out_master(tmp_path: Path) -> None:
    """A commit on checked-out master moves the ref and keeps later worktree bytes."""
    hub = _init_hub(tmp_path)
    (hub / "README.md").write_text("committed\n", encoding="utf-8")
    assert _git(hub, "add", "README.md").returncode == 0
    (hub / "README.md").write_text("committed\nplus unstaged\n", encoding="utf-8")
    committed = _git(hub, "commit", "-m", "ordinary")
    assert committed.returncode == 0, committed.stderr
    assert (hub / "README.md").read_text(
        encoding="utf-8"
    ) == "committed\nplus unstaged\n"
    show = _git(hub, "show", "HEAD:README.md")
    assert show.stdout == "committed\n"


def test_commit_keeps_worktree_bytes_that_equal_old(tmp_path: Path) -> None:
    """Index matches the new commit and the worktree is still the old blob.

    The prepared hook sees the index already equal to the incoming blob.
    Checking that path out would replace the worktree with the commit.
    """
    hub = _init_hub(tmp_path)
    (hub / "README.md").write_text("committed\n", encoding="utf-8")
    assert _git(hub, "add", "README.md").returncode == 0
    (hub / "README.md").write_text("base\n", encoding="utf-8")
    committed = _git(hub, "commit", "-m", "ordinary")
    assert committed.returncode == 0, committed.stderr
    assert (hub / "README.md").read_text(encoding="utf-8") == "base\n"


def test_staged_third_blob_blocks_ref_move(tmp_path: Path) -> None:
    """A staged blob other than old or new blocks the ref and stays in the index."""
    hub = _init_hub(tmp_path)
    old, new = _commit_ahead(hub)
    (hub / "README.md").write_text("operator staged\n", encoding="utf-8")
    assert _git(hub, "add", "README.md").returncode == 0
    (hub / "README.md").write_text("base\n", encoding="utf-8")
    moved = _git(hub, "update-ref", "refs/heads/master", new, old)
    assert moved.returncode != 0, moved.stderr
    assert _git(hub, "rev-parse", "refs/heads/master").stdout.strip() == old
    assert (hub / "README.md").read_text(encoding="utf-8") == "base\n"
    staged = _git(hub, "show", ":README.md")
    assert staged.stdout == "operator staged\n"


def test_clean_symlink_land_updates_the_link(tmp_path: Path) -> None:
    """A clean symlink in the land diff is the link text, not the target body."""
    hub = tmp_path / "hub"
    hub.mkdir()
    assert _git(hub, "init", "-b", "master").returncode == 0
    assert _git(hub, "config", "user.email", "t@example.com").returncode == 0
    assert _git(hub, "config", "user.name", "t").returncode == 0
    assert _git(hub, "config", "core.hooksPath", str(_HOOKS_DIR)).returncode == 0
    (hub / "README.md").write_text("base\n", encoding="utf-8")
    (hub / "link").symlink_to("README.md")
    assert _git(hub, "add", "README.md", "link").returncode == 0
    assert _git(hub, "commit", "-m", "base").returncode == 0
    old = _git(hub, "rev-parse", "HEAD").stdout.strip()
    assert _git(hub, "checkout", "-b", "land").returncode == 0
    (hub / "link").unlink()
    (hub / "link").symlink_to("added-target")
    (hub / "added-target").write_text("t\n", encoding="utf-8")
    assert _git(hub, "add", "link", "added-target").returncode == 0
    assert _git(hub, "commit", "-m", "land").returncode == 0
    new = _git(hub, "rev-parse", "HEAD").stdout.strip()
    assert _git(hub, "checkout", "master").returncode == 0
    moved = _git(hub, "update-ref", "refs/heads/master", new, old)
    assert moved.returncode == 0, moved.stderr
    assert (hub / "link").readlink() == Path("added-target")
