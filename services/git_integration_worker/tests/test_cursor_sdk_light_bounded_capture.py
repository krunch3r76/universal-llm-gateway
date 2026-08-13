"""Unit tests for light-bounded disk/cortex-existence deliverable capture."""

from __future__ import annotations

from pathlib import Path

from services.git_integration_worker.cursor_sdk_light_bounded_capture import (
    extract_instructed_paths,
    light_bounded_capture_status,
)


class TestExtractInstructedPaths:
    def test_files_expected_inline_scheme_path(self) -> None:
        prose = (
            "files_expected: cortex://notes/system/threads/x.md\n"
            "contract: light-bounded\n"
        )
        assert extract_instructed_paths(prose) == ("notes/system/threads/x.md",)

    def test_files_expected_repo_relative_bullet(self) -> None:
        prose = (
            "files_expected:\n"
            "- tasks/journal/2026-06-30-review.md\n"
            "authority: lead\n"
        )
        assert extract_instructed_paths(prose) == (
            "tasks/journal/2026-06-30-review.md",
        )

    def test_citation_only_prose_returns_empty(self) -> None:
        prose = (
            "See tasks/specs/cursor-sdk-workspaces-full-scope.md for background. "
            "Also review docs/agent-guides/skills/foo.md."
        )
        assert extract_instructed_paths(prose) == ()

    def test_read_locus_cited_in_body_not_extracted(self) -> None:
        """877fe5-class — file:line read citation must not enter expected paths."""
        prose = (
            "scope: read routes/cursor_sdk.py:1813-1826 only\n"
            "out-of-scope: No checkout edits to routes/cursor_sdk.py\n"
            "files_expected: cortex://notes/system/reviews/challenge-r2.md\n"
        )
        paths = extract_instructed_paths(prose)
        assert paths == ("notes/system/reviews/challenge-r2.md",)
        assert "routes/cursor_sdk.py" not in paths

    def test_english_only_files_expected_returns_empty(self) -> None:
        prose = "files_expected: cortex seed artifacts + todo mint\n"
        assert extract_instructed_paths(prose) == ()

    def test_empty_prose_returns_empty(self) -> None:
        assert extract_instructed_paths("") == ()

    def test_duplicate_paths_deduped(self) -> None:
        prose = (
            "files_expected:\n"
            "- tasks/journal/x.md\n"
            "- tasks/journal/x.md\n"
        )
        assert extract_instructed_paths(prose) == ("tasks/journal/x.md",)

    def test_comma_separated_files_expected(self) -> None:
        prose = (
            "files_expected: cortex://analysis.py, cortex://report.json\n"
        )
        assert extract_instructed_paths(prose) == ("analysis.py", "report.json")

    def test_files_expected_none_token(self) -> None:
        assert extract_instructed_paths("files_expected: none\n") == ()


class TestLightBoundedCaptureStatus:
    def test_written_path_reports_complete(self, tmp_path: Path) -> None:
        source_repo = tmp_path / "repo"
        cortex_root = tmp_path / "cortex"
        source_repo.mkdir()
        cortex_root.mkdir()
        target = source_repo / "tasks" / "journal" / "x.md"
        target.parent.mkdir(parents=True)
        target.write_text("done\n", encoding="utf-8")
        status, reason = light_bounded_capture_status(
            ("tasks/journal/x.md",),
            source_repo=source_repo,
            cortex_root=cortex_root,
        )
        assert status == "complete"
        assert reason is None

    def test_written_to_cortex_sandbox_reports_complete(self, tmp_path: Path) -> None:
        source_repo = tmp_path / "repo"
        cortex_root = tmp_path / "cortex"
        source_repo.mkdir()
        cortex_root.mkdir()
        target = cortex_root / "notes" / "system" / "x.md"
        target.parent.mkdir(parents=True)
        target.write_text("done\n", encoding="utf-8")
        status, reason = light_bounded_capture_status(
            ("notes/system/x.md",),
            source_repo=source_repo,
            cortex_root=cortex_root,
        )
        assert status == "complete"
        assert reason is None

    def test_missing_path_reports_partial_with_divergence(self, tmp_path: Path) -> None:
        source_repo = tmp_path / "repo"
        cortex_root = tmp_path / "cortex"
        source_repo.mkdir()
        cortex_root.mkdir()
        status, reason = light_bounded_capture_status(
            ("tasks/journal/never-written.md",),
            source_repo=source_repo,
            cortex_root=cortex_root,
        )
        assert status == "partial"
        assert (
            reason
            == "divergence:light_bounded_path_absent:tasks/journal/never-written.md"
        )

    def test_wrote_elsewhere_still_flagged(self, tmp_path: Path) -> None:
        source_repo = tmp_path / "repo"
        cortex_root = tmp_path / "cortex"
        source_repo.mkdir()
        cortex_root.mkdir()
        elsewhere = source_repo / "tasks" / "journal" / "other.md"
        elsewhere.parent.mkdir(parents=True)
        elsewhere.write_text("wrong file\n", encoding="utf-8")
        status, reason = light_bounded_capture_status(
            ("tasks/journal/expected.md",),
            source_repo=source_repo,
            cortex_root=cortex_root,
        )
        assert status == "partial"
        assert (
            reason == "divergence:light_bounded_path_absent:tasks/journal/expected.md"
        )
