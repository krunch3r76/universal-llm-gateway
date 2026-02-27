"""
Programmatic assess handler: citation coverage check.

Verifies that every fact index present in the verified_facts formatted list
appears at least once as a [N] citation in the generated prose artifact.

By the time this handler is called, verified_facts has already been
transformed by _format_text_list into "[1] text\n[2] text\n..." (1-indexed).
"""

from __future__ import annotations

import re
from typing import Any

from pipeline_assess_registry import register_assess_handler

_INDEX_RE = re.compile(r"\[(\d+)\]")


def citation_coverage_check(resolved: dict[str, Any]) -> dict[str, Any]:
    """
    Check whether all fact indices from verified_facts are cited in the artifact.

    Returns {"action": "accept"} if all indices cited.
    Returns {"action": "revise", "missing_indices": [...], "reason": "..."} if not.
    """
    artifact: str = resolved.get("artifact", "")
    verified_facts: str = str(resolved.get("verified_facts", ""))

    # Extract expected indices from pre-formatted "[N] text" lines (1-based)
    expected = {int(m) for m in re.findall(r"^\[(\d+)\]", verified_facts, re.MULTILINE)}
    cited = {int(m) for m in _INDEX_RE.findall(artifact)}
    missing = sorted(expected - cited)

    if not missing:
        return {"action": "accept", "reason": "All facts cited."}

    return {
        "action": "revise",
        "missing_indices": missing,
        "reason": (
            f"Facts not cited: {missing}. "
            "Re-synthesize ensuring every fact index appears."
        ),
    }


register_assess_handler("citation_coverage_check", citation_coverage_check)
