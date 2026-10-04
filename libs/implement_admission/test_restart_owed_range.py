"""restart_owed_for_range over a git SHA span (friction a:37771)."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from implement_admission.restart_owed import (
    record_land_restart_receipt,
    restart_owed_for_range,
)

pytestmark = pytest.mark.offline


def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
    )


def _sha(repo: Path, ref: str = "HEAD") -> str:
    proc = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", ref],
        check=True,
        capture_output=True,
        text=True,
    )
    return proc.stdout.strip()


def _init(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-b", "master")
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "t")
    (repo / "README.md").write_text("seed\n", encoding="utf-8")
    _git(repo, "add", "README.md")
    _git(repo, "commit", "-m", "seed")
    return repo


def test_equal_shas_are_none(tmp_path: Path) -> None:
    repo = _init(tmp_path)
    tip = _sha(repo)
    assert restart_owed_for_range(repo, tip, tip) == "restart_owed: none"


def test_range_covers_every_commit_and_keeps_unmapped(tmp_path: Path) -> None:
    repo = _init(tmp_path)
    before = _sha(repo)
    loader = repo / "services/universal-stargate/systems/pipeline/registry/loader.py"
    loader.parent.mkdir(parents=True)
    loader.write_text("# loader\n", encoding="utf-8")
    _git(repo, "add", "services/universal-stargate/systems/pipeline/registry/loader.py")
    _git(repo, "commit", "-m", "stargate")
    unmapped = repo / "services/not-a-fleet-service/app.py"
    unmapped.parent.mkdir(parents=True)
    unmapped.write_text("# app\n", encoding="utf-8")
    _git(repo, "add", "services/not-a-fleet-service/app.py")
    _git(repo, "commit", "-m", "unmapped")
    vocab = repo / "libs/capability_tree/vocabulary.py"
    vocab.parent.mkdir(parents=True)
    vocab.write_text("# vocab\n", encoding="utf-8")
    _git(repo, "add", "libs/capability_tree/vocabulary.py")
    _git(repo, "commit", "-m", "vocab")
    after = _sha(repo)
    text = restart_owed_for_range(repo, before, after)
    assert text.startswith("restart_owed: stargate\n")
    assert "unmapped: services/not-a-fleet-service/app.py" in text


def test_empty_before_raises(tmp_path: Path) -> None:
    """An empty before is not ``git diff ..after`` (HEAD..after on the hub)."""
    repo = _init(tmp_path)
    with pytest.raises(ValueError):
        restart_owed_for_range(repo, "", _sha(repo))


def test_bad_sha_raises(tmp_path: Path) -> None:
    repo = _init(tmp_path)
    with pytest.raises(subprocess.CalledProcessError):
        restart_owed_for_range(repo, "0" * 40, "1" * 40)


def test_bad_sha_receipt_says_unavailable(tmp_path: Path) -> None:
    repo = _init(tmp_path)
    block = record_land_restart_receipt(
        repo,
        "0" * 40,
        "1" * 40,
        "advance_master_cas",
    )
    assert block == "restart_owed: unavailable (CalledProcessError)"
    receipt = repo / "tmp" / "reviews" / "land-receipts" / f"{'1' * 40}.md"
    text = receipt.read_text(encoding="utf-8")
    assert "restart_owed: unavailable (CalledProcessError)" in text
    assert "land_path: advance_master_cas" in text


def test_write_failure_rewrites_unavailable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _init(tmp_path)
    tip = _sha(repo)
    from implement_admission import restart_owed as owed

    real = owed._publish_receipt
    calls = {"n": 0}

    def _fail_once(path: Path, body: str) -> None:
        calls["n"] += 1
        if calls["n"] == 1:
            raise OSError("disk full")
        return real(path, body)

    monkeypatch.setattr(owed, "_publish_receipt", _fail_once)
    block = record_land_restart_receipt(repo, tip, tip, "advance_master_cas")
    assert block == "restart_owed: unavailable (OSError)"
    text = (repo / "tmp/reviews/land-receipts" / f"{tip}.md").read_text(
        encoding="utf-8"
    )
    assert "restart_owed: unavailable (OSError)" in text


def test_second_publish_failure_leaves_no_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _init(tmp_path)
    tip = _sha(repo)
    from implement_admission import restart_owed as owed

    def _always(path: Path, body: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("restart_owed: trunc", encoding="utf-8")
        raise OSError("disk full")

    monkeypatch.setattr(owed, "_publish_receipt", _always)
    block = record_land_restart_receipt(repo, tip, tip, "advance_master_cas")
    assert block == "restart_owed: unavailable (OSError)"
    assert not (repo / "tmp/reviews/land-receipts" / f"{tip}.md").exists()
