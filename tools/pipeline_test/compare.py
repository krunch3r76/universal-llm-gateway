"""Comparison service: unified diff and metrics between original and replayed output."""

from __future__ import annotations

import difflib

from .models import ComparisonResult


def compare_outputs(
    step_name: str,
    original_text: str,
    replay_text: str,
    call_label: str | None = None,
) -> ComparisonResult:
    """Generate a unified diff and metrics comparing original vs replay."""
    orig_lines = original_text.splitlines(keepends=True)
    replay_lines = replay_text.splitlines(keepends=True)

    diff = difflib.unified_diff(
        orig_lines,
        replay_lines,
        fromfile="original",
        tofile="replay",
        lineterm="",
    )
    unified_diff = "".join(diff)

    return ComparisonResult(
        step_name=step_name,
        call_label=call_label,
        original_text=original_text,
        replay_text=replay_text,
        unified_diff=unified_diff,
        length_delta=len(replay_text) - len(original_text),
        token_delta={},
    )


def format_comparison(result: ComparisonResult) -> str:
    """Human-readable comparison report."""
    lines: list[str] = []
    lines.append(f"Step: {result.step_name}")
    if result.call_label:
        lines.append(f"Call: {result.call_label}")
    lines.append(f"Original length: {len(result.original_text)} chars")
    lines.append(f"Replay length:   {len(result.replay_text)} chars")
    lines.append(f"Delta:           {result.length_delta:+d} chars")
    lines.append("")

    if not result.unified_diff:
        lines.append("No differences found (outputs are identical).")
    else:
        lines.append("--- Diff ---")
        lines.append(result.unified_diff)

    return "\n".join(lines)
