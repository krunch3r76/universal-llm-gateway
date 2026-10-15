"""Write-order tests for keep-set trim (archive before house-rules before fixture)."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from claude_bundles.keep_set_trim import (
    PRE_IMAGE_SHA,
    POST_TRIM_SHA,
    apply_keep_set_trim,
    archive_append_blocks,
    archive_has_k36_verbatim,
    trim_house_rules_body,
)

pytestmark = pytest.mark.offline

_BASELINE = (
    Path(__file__).resolve().parent
    / "testdata"
    / "12286-house-rules-baseline-e1c52845.txt"
)


def test_house_rules_not_written_until_archive_has_k36_verbatim(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import claude_bundles.keep_set_trim as kst

    archive = tmp_path / "archive.md"
    house = tmp_path / "house.md"
    fixture = tmp_path / "fixture.md"
    archive.write_text("#### unrelated\n", encoding="utf-8")
    baseline = _BASELINE.read_text(encoding="utf-8")
    house.write_text(baseline, encoding="utf-8")
    arch_sha = hashlib.sha256(archive.read_bytes()).hexdigest()
    monkeypatch.setattr(kst, "archive_append_blocks", lambda: "\n")
    with pytest.raises(ValueError, match="K36"):
        apply_keep_set_trim(
            archive,
            house,
            fixture,
            expected_archive_sha=arch_sha,
            expected_house_rules_sha=PRE_IMAGE_SHA,
        )
    assert hashlib.sha256(house.read_bytes()).hexdigest() == PRE_IMAGE_SHA


def test_fixture_copied_from_post_trim_house_rules_after_write(tmp_path: Path) -> None:
    archive = tmp_path / "archive.md"
    house = tmp_path / "house.md"
    fixture = tmp_path / "fixture.md"
    archive.write_text("# archive\n", encoding="utf-8")
    baseline = _BASELINE.read_text(encoding="utf-8")
    house.write_text(baseline, encoding="utf-8")
    arch_sha = hashlib.sha256(archive.read_bytes()).hexdigest()
    apply_keep_set_trim(
        archive,
        house,
        fixture,
        expected_archive_sha=arch_sha,
        expected_house_rules_sha=PRE_IMAGE_SHA,
    )
    assert archive_has_k36_verbatim(archive.read_text(encoding="utf-8"))
    hr_sha = hashlib.sha256(house.read_bytes()).hexdigest()
    fix_sha = hashlib.sha256(fixture.read_bytes()).hexdigest()
    assert hr_sha == fix_sha == POST_TRIM_SHA
    assert archive_append_blocks() in archive.read_text(encoding="utf-8")


def test_trim_post_image_matches_plan_sha() -> None:
    baseline = _BASELINE.read_text(encoding="utf-8")
    out = trim_house_rules_body(baseline)
    assert hashlib.sha256(out.encode()).hexdigest() == POST_TRIM_SHA
    assert len(out.encode("utf-8")) == 6928
