"""Audit report handler — generates markdown consolidation report.

Produces a detailed report summarizing what the dream state pipeline assessed,
classified into the three tiers (AUTO_APPLY, RECOMMEND, PROTECT), and writes
a Cortex session journal entry for boot narrative visibility.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any, override

from systems.pipeline.core.handlers.builtin import BaseHandler
from systems.pipeline.core.handlers.protocol import StepOutput
from transport_utils import DEFAULT_CORTEX_URL, make_async_client

logger = logging.getLogger(__name__)


def _build_report(
    cursor_data: dict[str, Any],
    collect_data: dict[str, Any],
    apply_data: dict[str, Any],
    cursor_save: dict[str, Any],
) -> str:
    """Build the markdown audit report."""
    now = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")
    dry_run = apply_data.get("dry_run", True)
    mode = "DRY RUN" if dry_run else "LIVE"
    total = collect_data.get("total_assertions", 0)
    batch_count = collect_data.get("batch_count", 0)

    taken = apply_data.get("actions_taken", [])
    skipped = apply_data.get("actions_skipped", [])
    protected = apply_data.get("actions_protected", [])
    errors = apply_data.get("error_count", 0)

    lines = [
        f"# Dream State Consolidation Report — {now}",
        f"**Mode:** {mode}",
        f"**Assertions assessed:** {total}",
        f"**Batches:** {batch_count}",
        "",
        "## Actions Summary",
        "",
        f"### Auto-Applied ({len(taken)})",
        "",
    ]

    if taken:
        for a in taken:
            dr = " [DRY RUN]" if a.get("dry_run") else ""
            lines.append(
                f"- assertion #{a['assertion_id']}: "
                f"{a['claim_snippet']} — {a.get('deprecation_reason', 'N/A')}"
                f"{dr}"
            )
    else:
        lines.append("_(none)_")

    lines.extend(
        [
            "",
            f"### Recommended for Review ({len(skipped)})",
            "",
        ]
    )

    if skipped:
        for a in skipped:
            lines.append(
                f"- assertion #{a['assertion_id']}: "
                f"relevance={a.get('relevance_score', '?')}, "
                f"deprecate={a.get('should_deprecate', False)}, "
                f"reason={a.get('deprecation_reason', 'N/A')}"
            )
    else:
        lines.append("_(none)_")

    lines.extend(
        [
            "",
            f"### Protected ({len(protected)})",
            "",
        ]
    )

    if protected:
        for a in protected:
            lines.append(
                f"- assertion #{a['assertion_id']}: "
                f"{a['claim_snippet']} — {a.get('protection_reason', '?')}"
            )
    else:
        lines.append("_(none)_")

    enrichments = [
        a
        for a in (*taken, *skipped, *protected)
        if a.get("enrichment_suggestions", {}).get("tags")
    ]
    lines.extend(["", "## Enrichment Suggestions", ""])
    if enrichments:
        for a in enrichments:
            tags = a["enrichment_suggestions"].get("tags", [])
            lines.append(f"- assertion #{a['assertion_id']}: add tags {tags}")
    else:
        lines.append("_(none)_")

    relationships = [
        a for a in (*taken, *skipped, *protected) if a.get("relationships")
    ]
    lines.extend(["", "## Relationship Discoveries", ""])
    if relationships:
        for a in relationships:
            for rel in a.get("relationships", []):
                lines.append(
                    f"- assertion #{a['assertion_id']} → "
                    f"assertion #{rel.get('target_assertion_id', '?')}: "
                    f"{rel.get('type', '?')}"
                )
    else:
        lines.append("_(none)_")

    if errors:
        lines.extend(["", f"## Errors: {errors}", ""])

    old_cursor = cursor_data.get("last_processed_id", "None")
    new_cursor = cursor_save.get("new_last_processed_id", "?")
    lines.extend(
        [
            "",
            "## Cursor",
            f"Previous: {old_cursor} → New: {new_cursor}",
        ]
    )

    return "\n".join(lines)


async def _write_journal(report_summary: str, dry_run: bool) -> None:
    """Write a Cortex session journal entry for the consolidation run."""
    try:
        now = datetime.now(UTC).isoformat()
        async with make_async_client(DEFAULT_CORTEX_URL, timeout=10.0) as client:
            await client.post(
                "/session-journals",
                json={
                    "timestamp": now,
                    "agent": "dream-state-pipeline",
                    "summary": report_summary[:500],
                    "domains": ["cortex", "consolidation"],
                    "decisions": [],
                    "open_items": (
                        ["Review dry-run report before enabling live mode"]
                        if dry_run
                        else []
                    ),
                },
            )
    except Exception:
        logger.warning("Failed to write Cortex journal for dream state run")


class ReportHandler(BaseHandler):
    step_type = "dream_state_report_v1"

    @override
    async def execute(self, step: Any, context: Any) -> StepOutput:
        cursor_out = context.get_output("load_cursor")
        collect_out = context.get_output("collect_assertions")
        apply_out = context.get_output("guarded_apply")
        save_out = context.get_output("save_cursor")

        cursor_data = cursor_out.json if cursor_out and cursor_out.json else {}
        collect_data = collect_out.json if collect_out and collect_out.json else {}
        apply_data = apply_out.json if apply_out and apply_out.json else {}
        cursor_save = save_out.json if save_out and save_out.json else {}

        report = _build_report(cursor_data, collect_data, apply_data, cursor_save)

        dry_run = apply_data.get("dry_run", True)
        taken_count = len(apply_data.get("actions_taken", []))
        skipped_count = len(apply_data.get("actions_skipped", []))
        protected_count = len(apply_data.get("actions_protected", []))
        summary = (
            f"Dream state {'dry-run' if dry_run else 'live'}: "
            f"{collect_data.get('total_assertions', 0)} assessed, "
            f"{taken_count} auto-apply, {skipped_count} recommend, "
            f"{protected_count} protected"
        )

        await _write_journal(summary, dry_run)

        result: dict[str, Any] = {
            "report": report,
            "summary": summary,
        }

        logger.info("Dream state report: %s", summary)
        return StepOutput(raw=report, json=result)
