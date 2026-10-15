"""Write-order tests for keep-set trim (archive before house-rules before fixture)."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
from durable_io.atomic import PreImageMismatchError, durable_write_text

from claude_bundles.keep_set_trim import (
    POST_TRIM_SHA,
    PRE_IMAGE_SHA,
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


def test_house_rules_drift_refuses_before_archive_append(tmp_path: Path) -> None:
    archive = tmp_path / "archive.md"
    house = tmp_path / "house.md"
    fixture = tmp_path / "fixture.md"
    archive.write_text("# archive\n", encoding="utf-8")
    house.write_text(_BASELINE.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    before = archive.read_bytes()
    arch_sha = hashlib.sha256(before).hexdigest()
    with pytest.raises(ValueError, match="house-rules sha drift"):
        apply_keep_set_trim(
            archive,
            house,
            fixture,
            expected_archive_sha=arch_sha,
            expected_house_rules_sha=PRE_IMAGE_SHA,
        )
    assert archive.read_bytes() == before
    assert not fixture.exists()


def test_apply_keep_set_trim_is_idempotent_on_retry(tmp_path: Path) -> None:
    archive = tmp_path / "archive.md"
    house = tmp_path / "house.md"
    fixture = tmp_path / "fixture.md"
    archive.write_text("# archive\n", encoding="utf-8")
    house.write_text(_BASELINE.read_text(encoding="utf-8"), encoding="utf-8")
    arch_sha = hashlib.sha256(archive.read_bytes()).hexdigest()
    apply_keep_set_trim(
        archive,
        house,
        fixture,
        expected_archive_sha=arch_sha,
        expected_house_rules_sha=PRE_IMAGE_SHA,
    )
    marker = "#### K36 a:39306"
    assert archive.read_text(encoding="utf-8").count(marker) == 1
    retry_sha = hashlib.sha256(archive.read_bytes()).hexdigest()
    apply_keep_set_trim(
        archive,
        house,
        fixture,
        expected_archive_sha=retry_sha,
        expected_house_rules_sha=POST_TRIM_SHA,
    )
    assert archive.read_text(encoding="utf-8").count(marker) == 1


def test_keep_set_trim_refuses_over_budget_post_image(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import claude_bundles.keep_set_trim as kst

    archive = tmp_path / "archive.md"
    house = tmp_path / "x-house-rules.md"
    fixture = tmp_path / "fixture.md"
    archive.write_text("# archive\n" + kst.archive_append_blocks(), encoding="utf-8")
    house.write_text(_BASELINE.read_text(encoding="utf-8"), encoding="utf-8")
    before = house.read_bytes()
    monkeypatch.setattr(kst, "trim_house_rules_body", lambda _text: "Q" * 9000)
    with pytest.raises(ValueError, match="house_rules.over_budget"):
        apply_keep_set_trim(
            archive,
            house,
            fixture,
            expected_archive_sha=hashlib.sha256(archive.read_bytes()).hexdigest(),
            expected_house_rules_sha=PRE_IMAGE_SHA,
        )
    assert house.read_bytes() == before


def test_keep_set_trim_write_is_cas_guarded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import claude_bundles.keep_set_trim as kst

    seen: list[str | None] = []
    real = durable_write_text

    def spy(dest, content, **kwargs):
        seen.append(kwargs.get("expected_pre_image"))
        return real(dest, content, **kwargs)

    monkeypatch.setattr(kst, "durable_write_text", spy)
    archive = tmp_path / "archive.md"
    house = tmp_path / "house.md"
    fixture = tmp_path / "fixture.md"
    archive.write_text("# archive\n", encoding="utf-8")
    house.write_text(_BASELINE.read_text(encoding="utf-8"), encoding="utf-8")
    arch_sha = hashlib.sha256(archive.read_bytes()).hexdigest()
    apply_keep_set_trim(
        archive,
        house,
        fixture,
        expected_archive_sha=arch_sha,
        expected_house_rules_sha=PRE_IMAGE_SHA,
    )
    assert arch_sha in seen
    assert PRE_IMAGE_SHA in seen
    with pytest.raises(PreImageMismatchError):
        durable_write_text(house, "nope", expected_pre_image="0" * 64)
    assert hashlib.sha256(house.read_bytes()).hexdigest() == POST_TRIM_SHA


def test_trim_post_image_matches_plan_sha() -> None:
    baseline = _BASELINE.read_text(encoding="utf-8")
    out = trim_house_rules_body(baseline)
    assert hashlib.sha256(out.encode()).hexdigest() == POST_TRIM_SHA
    assert len(out.encode("utf-8")) == 6928
