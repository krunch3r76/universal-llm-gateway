"""Unit tests for light-bounded disk/cortex-existence deliverable capture.

Covers write-imperative-window path extraction and the disk-verify completeness
signal that bypasses the implement-only baseline-diff machinery
(todo:cursor-sdk-deliverables-expected-light-bounded).
"""

from __future__ import annotations

from pathlib import Path

from services.git_integration_worker.cursor_sdk_light_bounded_capture import (
    extract_instructed_paths,
    light_bounded_capture_status,
)


class TestExtractInstructedPaths:
    def test_known_prefix_path_extracted_on_imperative_line(self) -> None:
        prose = "Write your findings to tasks/journal/2026-06-30-review.md when done."
        assert extract_instructed_paths(prose) == (
            "tasks/journal/2026-06-30-review.md",
        )

    def test_cortex_scheme_prefix_stripped(self) -> None:
        prose = "Save the analysis to cortex://notes/system/threads/x.md."
        assert extract_instructed_paths(prose) == ("notes/system/threads/x.md",)

    def test_citation_only_prose_returns_empty(self) -> None:
        prose = (
            "See tasks/specs/cursor-sdk-workspaces-full-scope.md for background. "
            "Also review docs/agent-guides/skills/foo.md."
        )
        assert extract_instructed_paths(prose) == ()

    def test_non_imperative_path_mention_returns_empty(self) -> None:
        prose = "Drop the summary at /tmp/summaries/report.md for review."
        assert extract_instructed_paths(prose) == ()

    def test_no_path_mentioned_returns_empty(self) -> None:
        prose = "Just answer the question inline, no file needed."
        assert extract_instructed_paths(prose) == ()

    def test_empty_prose_returns_empty(self) -> None:
        assert extract_instructed_paths("") == ()

    def test_duplicate_mentions_deduped_preserving_order(self) -> None:
        prose = "Write tasks/journal/x.md now. Confirm tasks/journal/x.md landed."
        assert extract_instructed_paths(prose) == ("tasks/journal/x.md",)

    def test_imperative_next_line_extracts_path(self) -> None:
        prose = "Write the deliverable here:\nlibs/foo/bar.py"
        assert extract_instructed_paths(prose) == ("libs/foo/bar.py",)

    def test_skeptic_o1_falsifier_extracts_two_scheme_paths(self) -> None:
        prose = (
            "Write a comprehensive analysis script … Save the script and its "
            "output report.\n\n"
            "cortex://analysis.py\n"
            "cortex://report.json"
        )
        assert extract_instructed_paths(prose) == ("analysis.py", "report.json")

    def test_skeptic_o1_round2_trailing_descriptions_ignored(self) -> None:
        prose = (
            "Write the core modules to durable storage.\n\n"
            "cortex://core_logic.py - contains the main algorithm\n"
            "cortex://status_utils.py - handles status reporting"
        )
        assert extract_instructed_paths(prose) == ("core_logic.py", "status_utils.py")


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
        """Writing to a different path than named must not satisfy the check."""
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
