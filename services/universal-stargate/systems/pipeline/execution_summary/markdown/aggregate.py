"""
Aggregate-step parent-rejection markdown block.

Builds the ``## Aggregate Summary`` markdown section for pipeline steps whose
``step_id`` contains ``aggregate``. Emits the parent claims that were rejected
on a strict-math sub-claim, each annotated with the authority (e.g. qwen-math)
reasons that caused the failure — so a summary explains *why* without relying
on truncated logs.

Returns an empty list for non-aggregate steps or aggregate steps with no
math-rejected parents — callers extend lines unconditionally.
"""

from __future__ import annotations

from typing import Any


def build_aggregate_summary(step_id: str, output: Any) -> list[str]:
    """
    Build markdown for aggregate-step parent rejections (math).

    Args:
        step_id: Step identifier — function is a no-op unless this contains
            ``aggregate`` (case-insensitive).
        output: Step output object. Must expose a ``.json`` attribute that is a
            dict shaped ``{"candidates": [{"aggregated_verdict": bool,
            "sub_claim_stats": {"math_strict": ...},
            "statement_id": str, "text": str,
            "sub_claim_verdicts": {sid: {"passes": bool}},
            "failed_sub_reasons": {sid: str}}, ...]}``.

    Returns:
        List of markdown lines (empty if the step is not an aggregate step or
        has no math-rejected parents).
    """
    if "aggregate" not in step_id.lower():
        return []

    if not getattr(output, "json", None) or not isinstance(output.json, dict):
        return []

    candidates = output.json.get("candidates", [])
    rejected_math = [
        c
        for c in candidates
        if c.get("aggregated_verdict") is False
        and c.get("sub_claim_stats", {}).get("math_strict")
    ]
    if not rejected_math:
        return []

    lines = [
        "",
        "## Aggregate Summary",
        "",
        "### Parent rejections (math)",
        "",
        "Authority (e.g. qwen-math) reasons for each failed sub-claim below.",
        "",
    ]
    for c in rejected_math:
        stmt_id = c.get("statement_id", "unknown")
        text = (c.get("text") or "")[:250]
        if len(c.get("text") or "") > 250:
            text += "..."
        failed_subs = [
            sid
            for sid, sv in (c.get("sub_claim_verdicts") or {}).items()
            if not sv.get("passes")
        ]
        reasons = c.get("failed_sub_reasons") or {}

        lines.append(f"**Parent**: `{stmt_id}`")
        lines.append(f"- **Text**: {text}")
        lines.append(f"- **Failed sub-claims**: {failed_subs}")
        for sid in failed_subs:
            reason = reasons.get(sid, "(no reason in output)")
            if len(reason) > 400:
                reason = reason[:400] + "..."
            lines.append(f"  - `{sid}`: {reason}")
        lines.append("")

    return lines
