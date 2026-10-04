"""Hub-master moves record restart_owed from the git range (friction a:37771)."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from services.git_integration_worker.cursor_sdk_hub_land_scope import (
    clean_merge_onto_hub_master,
    ff_only_onto_hub_master,
)

pytestmark = pytest.mark.offline


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        check=False,
    )


def _init_hub(tmp_path: Path) -> Path:
    repo = tmp_path / "hub"
    repo.mkdir()
    assert _git(repo, "init", "-b", "master").returncode == 0
    assert _git(repo, "config", "user.email", "t@example.com").returncode == 0
    assert _git(repo, "config", "user.name", "t").returncode == 0
    (repo / "README.md").write_text("seed\n", encoding="utf-8")
    assert _git(repo, "add", "README.md").returncode == 0
    assert _git(repo, "commit", "-m", "seed").returncode == 0
    return repo


def _commit_on_branch(repo: Path, branch: str, files: dict[str, str], message: str) -> None:
    assert _git(repo, "checkout", "-b", branch).returncode == 0
    for rel, body in files.items():
        path = repo / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
        assert _git(repo, "add", rel).returncode == 0
    assert _git(repo, "commit", "-m", message).returncode == 0
    assert _git(repo, "checkout", "master").returncode == 0


def _receipt(repo: Path) -> str:
    after = _git(repo, "rev-parse", "refs/heads/master").stdout.strip()
    path = repo / "tmp" / "reviews" / "land-receipts" / f"{after}.md"
    return path.read_text(encoding="utf-8")


def test_ff_only_specimen_receipt_names_stargate(tmp_path: Path) -> None:
    """a:37771: full stargate paths, not a hand-built list with the prefix dropped."""
    repo = _init_hub(tmp_path)
    _commit_on_branch(
        repo,
        "cursor-sdk/lane-specimen",
        {
            "services/universal-stargate/systems/pipeline/registry/loader.py": (
                "# loader\n"
            ),
            "libs/capability_tree/vocabulary.py": "# vocab\n",
        },
        "specimen paths",
    )
    assert ff_only_onto_hub_master(repo, branch_name="cursor-sdk/lane-specimen") is True
    text = _receipt(repo)
    assert "restart_owed: stargate" in text
    assert "land_path: ff_only_onto_hub_master" in text
    assert "unmapped:" not in text


def test_ff_only_refusal_writes_no_receipt(tmp_path: Path) -> None:
    repo = _init_hub(tmp_path)
    assert ff_only_onto_hub_master(repo, branch_name="master") is False
    assert not (repo / "tmp" / "reviews" / "land-receipts").exists()


def test_clean_merge_records_receipt(tmp_path: Path) -> None:
    repo = _init_hub(tmp_path)
    assert _git(repo, "checkout", "-b", "peer").returncode == 0
    (repo / "peer.md").write_text("peer\n", encoding="utf-8")
    assert _git(repo, "add", "peer.md").returncode == 0
    assert _git(repo, "commit", "-m", "peer").returncode == 0
    assert _git(repo, "checkout", "master").returncode == 0
    (repo / "hub.md").write_text("hub\n", encoding="utf-8")
    assert _git(repo, "add", "hub.md").returncode == 0
    assert _git(repo, "commit", "-m", "hub").returncode == 0
    assert _git(repo, "checkout", "peer").returncode == 0
    (repo / "services/universal-stargate/peer_only.py").parent.mkdir(parents=True)
    (repo / "services/universal-stargate/peer_only.py").write_text(
        "# peer only\n", encoding="utf-8"
    )
    assert _git(repo, "add", "services/universal-stargate/peer_only.py").returncode == 0
    assert _git(repo, "commit", "-m", "peer stargate").returncode == 0
    assert _git(repo, "checkout", "master").returncode == 0
    assert clean_merge_onto_hub_master(repo, branch_name="peer") is True
    text = _receipt(repo)
    assert "restart_owed: stargate" in text
    assert "land_path: clean_merge_onto_hub_master" in text


def test_ff_diff_failure_still_lands(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = _init_hub(tmp_path)
    _commit_on_branch(
        repo,
        "cursor-sdk/lane-unreadable",
        {"libs/capability_tree/vocabulary.py": "# vocab\n"},
        "vocab",
    )

    def _boom(*_args: object, **_kwargs: object) -> subprocess.CompletedProcess[str]:
        raise OSError("unreadable repo")

    monkeypatch.setattr("implement_admission.restart_owed.subprocess.run", _boom)
    assert (
        ff_only_onto_hub_master(repo, branch_name="cursor-sdk/lane-unreadable") is True
    )
    text = _receipt(repo)
    assert "restart_owed: unavailable (OSError)" in text
