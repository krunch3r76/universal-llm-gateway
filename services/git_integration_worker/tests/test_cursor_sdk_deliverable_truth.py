"""Unit tests for the light-bounded closeout-truth backstop (friction 21654 fix #3).

Covers both signal paths — the structured tool_calls-derived choke signal and
the source-independent stated-intent-no-write tell — plus contract gating and
the robustness case where the #1 stream observation is empty.
"""

from __future__ import annotations

from pathlib import Path

from services.git_integration_worker.cursor_sdk_deliverable_truth import (
    deliverable_write_choke_reason,
    light_bounded_deliverable_reason,
    stated_intent_no_write_reason,
)
from services.git_integration_worker.cursor_sdk_light_bounded_capture import (
    light_bounded_deliverable_present,
)
from services.git_integration_worker.cursor_sdk_stream_capture import (
    ToolCallObservation,
)


def _tc(
    *,
    tool_name: str = "fs",
    status: str = "completed",
    arg_bytes: int = 0,
    result_bytes: int = 0,
    truncated_fields: tuple[str, ...] = (),
    call_id: str = "c1",
) -> ToolCallObservation:
    return ToolCallObservation(
        call_id=call_id,
        tool_name=tool_name,
        status=status,
        arg_bytes=arg_bytes,
        result_bytes=result_bytes,
        truncated_fields=truncated_fields,
    )


def _landed_write() -> ToolCallObservation:
    return _tc(tool_name="fs", status="completed", arg_bytes=4096)


def _read_call() -> ToolCallObservation:
    return _tc(tool_name="fs", status="completed", arg_bytes=80, result_bytes=9000)


class TestChokeSignal:
    def test_errored_write_with_no_landed_write_degrades(self) -> None:
        calls = (_tc(tool_name="fs", status="error", arg_bytes=200_000),)
        assert deliverable_write_choke_reason(calls) == "deliverable_write_choked"

    def test_arg_truncated_write_degrades(self) -> None:
        calls = (
            _tc(
                tool_name="fs",
                status="completed",
                arg_bytes=200_000,
                truncated_fields=("content",),
            ),
        )
        assert deliverable_write_choke_reason(calls) == "deliverable_write_choked"

    def test_result_only_truncation_is_not_a_choke(self) -> None:
        # Result-side truncation is a large-read concern, not a failed write.
        calls = (
            _tc(
                tool_name="fs",
                status="completed",
                arg_bytes=80,
                truncated_fields=("result",),
            ),
        )
        assert deliverable_write_choke_reason(calls) is None

    def test_landed_write_after_failed_attempt_clears(self) -> None:
        calls = (
            _tc(tool_name="fs", status="error", arg_bytes=200_000, call_id="c1"),
            _tc(tool_name="fs", status="completed", arg_bytes=4096, call_id="c2"),
        )
        assert deliverable_write_choke_reason(calls) is None

    def test_reads_only_do_not_degrade(self) -> None:
        assert deliverable_write_choke_reason((_read_call(),)) is None

    def test_empty_tool_calls_no_choke(self) -> None:
        assert deliverable_write_choke_reason(()) is None

    def test_prefixed_mcp_tool_name_is_write_family(self) -> None:
        calls = (_tc(tool_name="mcp_user-vortex_fs", status="error", arg_bytes=99_000),)
        assert deliverable_write_choke_reason(calls) == "deliverable_write_choked"


class TestStatedIntentNoWrite:
    def test_intent_cortex_scheme_no_write_degrades(self) -> None:
        body = "Analysis complete. I'll write the review to cortex://notes/system/x.md."
        assert stated_intent_no_write_reason(body, ()) == "stated_intent_no_write"

    def test_intent_bare_path_no_write_degrades(self) -> None:
        body = "Saved the results to notes/system/threads/report.md as requested."
        assert stated_intent_no_write_reason(body, ()) == "stated_intent_no_write"

    def test_intent_fs_call_prose_no_write_degrades(self) -> None:
        body = 'Writing the spec now via fs(sandbox="workspaces", op="write").'
        assert stated_intent_no_write_reason(body, ()) == "stated_intent_no_write"

    def test_intent_with_landed_write_clears(self) -> None:
        body = "I'll write the report to cortex://notes/system/x.md."
        assert stated_intent_no_write_reason(body, (_landed_write(),)) is None

    def test_intent_with_only_reads_still_degrades(self) -> None:
        body = "Writing results to docs/report.md."
        assert (
            stated_intent_no_write_reason(body, (_read_call(),))
            == "stated_intent_no_write"
        )

    def test_no_write_intent_does_not_degrade(self) -> None:
        body = "The answer to your question is 42. No files were involved."
        assert stated_intent_no_write_reason(body, ()) is None

    def test_verb_far_from_path_does_not_match(self) -> None:
        body = (
            "I will now explain the design. " + ("filler " * 60) + "notes/system/x.md"
        )
        assert stated_intent_no_write_reason(body, ()) is None


class TestContractGate:
    def test_non_light_bounded_never_degrades(self) -> None:
        calls = (_tc(tool_name="fs", status="error", arg_bytes=200_000),)
        body = "I'll write the report to cortex://notes/system/x.md."
        for contract in ("implement", "consult", "pure-mechanical"):
            assert (
                light_bounded_deliverable_reason(
                    body=body, tool_calls=calls, contract=contract
                )
                is None
            )

    def test_light_bounded_choke_precedes_intent(self) -> None:
        calls = (_tc(tool_name="fs", status="error", arg_bytes=200_000),)
        body = "I'll write the report to cortex://notes/system/x.md."
        assert (
            light_bounded_deliverable_reason(
                body=body, tool_calls=calls, contract="light-bounded"
            )
            == "deliverable_write_choked"
        )

    def test_light_bounded_stated_intent_when_no_structured_signal(self) -> None:
        body = "Saved to notes/system/threads/report.md."
        assert (
            light_bounded_deliverable_reason(
                body=body, tool_calls=(), contract="light-bounded"
            )
            == "stated_intent_no_write"
        )

    def test_light_bounded_clean_write_is_complete(self) -> None:
        body = "I wrote the review to cortex://notes/system/x.md."
        assert (
            light_bounded_deliverable_reason(
                body=body, tool_calls=(_landed_write(),), contract="light-bounded"
            )
            is None
        )


class TestDeliverablePresentSuppression:
    """Existence ground truth suppresses the light-bounded degrade at birth
    (todo:cursor-sdk-sidecar-write-detection-gap, assertion 22423)."""

    def test_present_deliverable_suppresses_stated_intent(self) -> None:
        # Cortex sidecar landed but the stream never surfaced it (empty tool_calls).
        body = "Saved to notes/system/threads/report.md."
        assert (
            light_bounded_deliverable_reason(
                body=body,
                tool_calls=(),
                contract="light-bounded",
                deliverable_present=True,
            )
            is None
        )

    def test_present_deliverable_suppresses_choke(self) -> None:
        calls = (_tc(tool_name="fs", status="error", arg_bytes=200_000),)
        body = "I'll write the report to cortex://notes/system/x.md."
        assert (
            light_bounded_deliverable_reason(
                body=body,
                tool_calls=calls,
                contract="light-bounded",
                deliverable_present=True,
            )
            is None
        )

    def test_absent_deliverable_still_degrades(self) -> None:
        # No false-positive suppression: a genuine no-write still degrades.
        body = "Saved to notes/system/threads/report.md."
        assert (
            light_bounded_deliverable_reason(
                body=body,
                tool_calls=(),
                contract="light-bounded",
                deliverable_present=False,
            )
            == "stated_intent_no_write"
        )

    def test_default_is_no_suppression(self) -> None:
        # Back-compat: omitting deliverable_present preserves prior behavior.
        body = "Saved to notes/system/threads/report.md."
        assert (
            light_bounded_deliverable_reason(
                body=body, tool_calls=(), contract="light-bounded"
            )
            == "stated_intent_no_write"
        )


class TestLightBoundedDeliverablePresent:
    """Shared existence predicate: named-paths-only presence over source_repo
    or cortex sandbox; empty => False; all-present required."""

    def test_present_in_cortex_sandbox(self, tmp_path: Path) -> None:
        source_repo = tmp_path / "repo"
        cortex_root = tmp_path / "cortex"
        target = cortex_root / "notes" / "system" / "threads" / "s.md"
        target.parent.mkdir(parents=True)
        target.write_text("x")
        assert light_bounded_deliverable_present(
            ("notes/system/threads/s.md",),
            source_repo=source_repo,
            cortex_root=cortex_root,
        )

    def test_present_in_source_repo(self, tmp_path: Path) -> None:
        source_repo = tmp_path / "repo"
        cortex_root = tmp_path / "cortex"
        target = source_repo / "tasks" / "specs" / "spec.md"
        target.parent.mkdir(parents=True)
        target.write_text("x")
        assert light_bounded_deliverable_present(
            ("tasks/specs/spec.md",),
            source_repo=source_repo,
            cortex_root=cortex_root,
        )

    def test_absent_path_is_false(self, tmp_path: Path) -> None:
        assert not light_bounded_deliverable_present(
            ("notes/system/threads/missing.md",),
            source_repo=tmp_path / "repo",
            cortex_root=tmp_path / "cortex",
        )

    def test_one_of_two_absent_is_false(self, tmp_path: Path) -> None:
        cortex_root = tmp_path / "cortex"
        present = cortex_root / "notes" / "system" / "a.md"
        present.parent.mkdir(parents=True)
        present.write_text("x")
        assert not light_bounded_deliverable_present(
            ("notes/system/a.md", "notes/system/b.md"),
            source_repo=tmp_path / "repo",
            cortex_root=cortex_root,
        )

    def test_empty_expected_paths_is_false(self, tmp_path: Path) -> None:
        assert not light_bounded_deliverable_present(
            (),
            source_repo=tmp_path / "repo",
            cortex_root=tmp_path / "cortex",
        )
