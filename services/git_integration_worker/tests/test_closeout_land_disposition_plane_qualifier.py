"""arc 12286 — qualify bare ``land_disposition: landed`` with plane vocabulary (13157 specimen)."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from services.git_integration_worker.cursor_sdk_branch_terminal import (
    parse_land_disposition,
)

pytestmark = pytest.mark.offline


def _git(repo: Path, *args: str) -> str:
    return subprocess.check_output(
        ["git", "-C", str(repo), *args],
        text=True,
    ).strip()


def _init_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "-C", str(repo), "init", "-b", "master"], check=True)
    subprocess.run(
        ["git", "-C", str(repo), "config", "user.email", "t@example.com"],
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(repo), "config", "user.name", "t"],
        check=True,
    )
    (repo / "README.md").write_text("seed\n", encoding="utf-8")
    _git(repo, "add", "README.md")
    _git(repo, "commit", "-m", "seed")
    return repo


def _wrapper(*, head_sha: str, branch: str) -> str:
    return json.dumps(
        {
            "schema_version": 1,
            "status": "complete",
            "capture_status": "complete",
            "files_created": [],
            "head_sha": head_sha,
            "branch": branch,
            "commits_ahead": 1,
            "landed": False,
        }
    )






@pytest.mark.parametrize(
    ("line", "expected_verb"),
    [
        ("land_disposition: landed@local-master", "landed"),
        (
            "land_disposition: tip@lane-B(cursor-sdk/lane-13157) · NOT landed@local-master",
            "landed",
        ),
        ("land_disposition: discard", "discard"),
    ],
)
def test_parse_land_disposition_reads_qualified_landed(line: str, expected_verb: str) -> None:
    verb, _reason, _sha = parse_land_disposition(line)
    assert verb == expected_verb
