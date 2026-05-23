"""--check diff helpers + core_ws.mdc table extraction for gen_rules."""

from __future__ import annotations

import difflib
from pathlib import Path

_CORE_TABLE_HEADING = "## Subagent Model Selection (quick ref)"
_NEXT_H2 = "## Logs"


def extract_core_subagent_table(core_mdc_path: Path) -> str:
    """Extract the `## Subagent Model Selection (quick ref)` section from core_ws.mdc.

    Returns the section verbatim from its `##` heading through the line preceding the
    next H2. Raises if either boundary is missing — drift in core_ws.mdc structure must
    surface, not be silently absorbed.
    """
    text = core_mdc_path.read_text()
    lines = text.splitlines(keepends=True)
    start = None
    end = None
    for i, ln in enumerate(lines):
        if ln.startswith(_CORE_TABLE_HEADING):
            start = i
            continue
        if start is not None and ln.startswith(_NEXT_H2):
            end = i
            break
    if start is None:
        raise ValueError(
            f"ERROR: heading {_CORE_TABLE_HEADING!r} not found in {core_mdc_path}"
        )
    if end is None:
        raise ValueError(
            f"ERROR: next H2 {_NEXT_H2!r} not found after subagent table in {core_mdc_path}"
        )
    section = "".join(lines[start:end])
    return section.rstrip() + "\n"


def diff_against(
    expected: str, actual: str, *, label_expected: str, label_actual: str
) -> str:
    """Return unified diff (empty string if equal)."""
    if expected == actual:
        return ""
    diff = difflib.unified_diff(
        expected.splitlines(keepends=True),
        actual.splitlines(keepends=True),
        fromfile=label_expected,
        tofile=label_actual,
        n=3,
    )
    return "".join(diff)
