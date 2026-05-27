"""
Verification-step markdown block — failed/passed enumeration + per-model votes.

For pipeline steps whose ``step_id`` starts with ``verify`` or contains
``verification``, builds a ``## Verification Summary`` section enumerating
which statements passed, which failed, per-model vote breakdowns, and
representative failure reasons. Statement text is resolved from the step's
``handler_inputs`` so summaries display the full claim, not just the ID.

Module-private helper ``build_statement_lookup`` is used internally by
``build_verification_summary``; it is not part of the markdown package's
public surface.
"""

from __future__ import annotations

from collections import defaultdict
from typing import TYPE_CHECKING, Any

from universal_logging import get_logger

from ..execution_summary_inputs import resolve_handler_inputs

if TYPE_CHECKING:
    from ...core.handlers.protocol import PipelineContext

logger = get_logger(__name__)


def build_statement_lookup(
    spec: Any, context: PipelineContext | None
) -> dict[str, dict]:
    """
    Build a lookup table from ``statement_id`` to its statement data.

    Extracts the original statements from the step's ``handler_inputs`` so
    verification summaries can show full statement text rather than only IDs.

    Args:
        spec: Step specification (may be ``None`` — returns empty dict).
        context: Pipeline context (may be ``None`` — returns empty dict).

    Returns:
        Dict mapping ``statement_id`` → statement dict. Empty dict on any
        resolution failure (logged at DEBUG).
    """
    if not spec or not context:
        return {}

    try:
        resolved = resolve_handler_inputs(spec, context)
        if not resolved:
            return {}

        if "statements" in resolved:
            _, statements = resolved["statements"]
            if isinstance(statements, list):
                lookup = {}
                for stmt in statements:
                    if isinstance(stmt, dict) and "statement_id" in stmt:
                        lookup[stmt["statement_id"]] = stmt
                return lookup

    except Exception as e:
        logger.debug(f"Could not build statement lookup: {e}")

    return {}


def build_verification_summary(
    all_outputs: list[Any],
    step_id: str,
    spec: Any = None,
    context: PipelineContext | None = None,
) -> list[str]:
    """
    Build enhanced summary section for verification steps.

    Shows:
    - Which statements failed (full text, not just reasons)
    - Which statements passed
    - Per-model breakdown statistics
    - Sub-claim/parent relationships

    Args:
        all_outputs: Verification outputs from each model iteration.
        step_id: ID of the verification step — function is a no-op unless this
            starts with ``verify`` or contains ``verification``.
        spec: Step specification (for accessing ``handler_inputs``).
        context: Pipeline context (for resolving statement data).

    Returns:
        Markdown lines (empty if not a verification step or no evaluations).
    """
    is_verification = step_id.startswith("verify") or "verification" in step_id.lower()
    if not is_verification:
        return []

    evaluations_with_model = []  # (evaluation, model_id)
    for iter_output in all_outputs:
        if hasattr(iter_output, "json") and isinstance(iter_output.json, dict):
            evals = iter_output.json.get("evaluations", [])
            model_id = getattr(iter_output, "model_id", "unknown")
            for eval_item in evals:
                evaluations_with_model.append((eval_item, model_id))

    if not evaluations_with_model:
        return []

    statement_lookup = build_statement_lookup(spec, context)

    # Aggregate verdicts by statement_id (across all models)
    stmt_verdicts: dict[str, list[tuple[str, bool, str]]] = defaultdict(
        list
    )  # stmt_id -> [(model, verdict, reason)]
    for eval_item, model_id in evaluations_with_model:
        stmt_id = eval_item.get("statement_id", "unknown")
        verdict = eval_item.get("verdict", False)
        reason = eval_item.get("reason", "")
        stmt_verdicts[stmt_id].append((model_id, verdict, reason))

    # Majority-vote per statement
    passed_stmt_ids = set()
    failed_stmt_ids = set()
    for stmt_id, verdicts in stmt_verdicts.items():
        pass_count = sum(1 for _, v, _ in verdicts if v)
        fail_count = len(verdicts) - pass_count
        if pass_count > fail_count:
            passed_stmt_ids.add(stmt_id)
        else:
            failed_stmt_ids.add(stmt_id)

    total_statements = len(stmt_verdicts)
    passed_count = len(passed_stmt_ids)
    pass_rate = (passed_count / total_statements * 100) if total_statements > 0 else 0

    lines = [
        "",
        "## Verification Summary",
        "",
        (
            f"**Overall**: {passed_count}/{total_statements} statements "
            f"passed ({pass_rate:.1f}%)"
        ),
        "",
    ]

    unique_models = sorted(
        {model for _, model in evaluations_with_model if model != "unknown"}
    )
    if len(unique_models) > 1:
        lines.extend(["### Verification by Model", ""])
        lines.append("| Model | Passed | Failed | Pass Rate |")
        lines.append("|-------|--------|--------|-----------|")

        for model in unique_models:
            model_evals = [e for e, m in evaluations_with_model if m == model]
            model_passed = sum(1 for e in model_evals if e.get("verdict", False))
            model_total = len(model_evals)
            model_rate = (model_passed / model_total * 100) if model_total > 0 else 0
            display_name = model if len(model) <= 30 else model[:27] + "..."
            lines.append(
                f"| {display_name} | {model_passed}/{model_total} | "
                f"{model_total - model_passed}/{model_total} | {model_rate:.1f}% |"
            )

        lines.append("")

    if failed_stmt_ids:
        lines.extend(
            [
                f"### ❌ Failed Verification ({len(failed_stmt_ids)} statements)",
                "",
            ]
        )

        for stmt_id in sorted(failed_stmt_ids):
            stmt_info = statement_lookup.get(stmt_id, {})
            text = stmt_info.get("text", "")
            is_sub_claim = stmt_info.get("is_sub_claim", False)
            parent_id = stmt_info.get("parent_id")

            if len(text) > 200:
                text = text[:200] + "..."

            lines.append(f"**Statement**: {text or f'[{stmt_id}]'}")
            lines.append(f"- **ID**: `{stmt_id}`")

            if is_sub_claim and parent_id:
                parent_info = statement_lookup.get(parent_id, {})
                parent_text = parent_info.get("text", parent_id)
                if len(parent_text) > 150:
                    parent_text = parent_text[:150] + "..."
                lines.append(f"- **Parent**: {parent_text}")

            verdicts = stmt_verdicts[stmt_id]
            if len(unique_models) > 1:
                verdict_summary = []
                for model_id, verdict, _ in verdicts:
                    symbol = "✅" if verdict else "❌"
                    short_model = (
                        model_id.split(":")[-1] if ":" in model_id else model_id
                    )
                    verdict_summary.append(f"{short_model} {symbol}")
                lines.append(f"- **Verdicts**: {', '.join(verdict_summary)}")

            failed_reasons = [r for _, v, r in verdicts if not v and r]
            if failed_reasons:
                reason = failed_reasons[0]
                if len(reason) > 150:
                    reason = reason[:150] + "..."
                lines.append(f"- **Reason**: {reason}")

            lines.append("")

    if passed_stmt_ids:
        lines.extend(
            [
                f"### ✅ Passed Verification ({len(passed_stmt_ids)} statements)",
                "",
            ]
        )

        parents_with_subs = []
        atomic_statements = []
        sub_claims = []

        for stmt_id in sorted(passed_stmt_ids):
            stmt_info = statement_lookup.get(stmt_id, {})
            has_sub_claims = stmt_info.get("has_sub_claims", False)
            is_sub_claim = stmt_info.get("is_sub_claim", False)

            if has_sub_claims:
                parents_with_subs.append(stmt_id)
            elif is_sub_claim:
                sub_claims.append(stmt_id)
            else:
                atomic_statements.append(stmt_id)

        if parents_with_subs:
            lines.append(f"**Parent Claims** ({len(parents_with_subs)} passed):")
            for stmt_id in parents_with_subs[:10]:
                stmt_info = statement_lookup.get(stmt_id, {})
                text = stmt_info.get("text", stmt_id)
                if len(text) > 150:
                    text = text[:150] + "..."
                sub_claim_ids = stmt_info.get("sub_claim_ids", [])
                lines.append(f"- {text} (`{stmt_id}`)")
                if sub_claim_ids:
                    passed_subs = sum(
                        1 for sc in sub_claim_ids if sc in passed_stmt_ids
                    )
                    lines.append(
                        f"  - Sub-claims: {passed_subs}/{len(sub_claim_ids)} passed"
                    )
            if len(parents_with_subs) > 10:
                lines.append(
                    f"\n*... ({len(parents_with_subs) - 10} more parent claims)*"
                )
            lines.append("")

        if atomic_statements:
            lines.append(f"**Atomic Statements** ({len(atomic_statements)} passed):")
            for stmt_id in atomic_statements[:10]:
                stmt_info = statement_lookup.get(stmt_id, {})
                text = stmt_info.get("text", stmt_id)
                if len(text) > 150:
                    text = text[:150] + "..."
                lines.append(f"- {text} (`{stmt_id}`)")
            if len(atomic_statements) > 10:
                lines.append(f"\n*... ({len(atomic_statements) - 10} more)*")
            lines.append("")

        if sub_claims:
            lines.append(
                f"**Sub-Claims** ({len(sub_claims)} passed - "
                "shown with parent context above)"
            )
            lines.append("")

    return lines
