"""Housekeeping-only field assembly for hop-successor prompts."""

from __future__ import annotations

from collections.abc import Callable

from hop_handoff.standing_handoff import standing_handoff_uri

from claude_bundles.journal_hop_projection import (
    MAESTRO_JOURNAL_URI,
    MAESTRO_REGISTER_URI,
    ReadJournalHead,
    hop_journal_projection_fields,
)
from claude_bundles.maestro_runbook_load import load_maestro_runbook
from claude_bundles.operator_proxy_standing_render import (
    measure_checkpoint_budget_bytes,
    render_budgets_table_block,
    runbook_refuse_steps_bytes,
)


def _render_failed(block: str, exc: BaseException, uri: str) -> str:
    return f"[render_failed {block}: {type(exc).__name__} — read {uri}]"


def housekeeping_fields(
    *,
    lane: str,
    hop_kind: str,
    hop_admitted_at_display: str,
    responsibility_display: str,
    birth: str,
    execution_clause: str,
    digest: str,
    running_now_line: str,
    house_rules_verbatim: str,
    checkpoint_block: str,
    inflight_block: str,
    fetch_last: int,
    sidecar: str,
    handoff_failure: str,
    inline_rules: str | None,
    journal_text: str | None,
    register_text: str | None,
    read_cortex_uri: Callable[[str], str | None] | None,
    read_journal_head: ReadJournalHead | None,
    inflight_projection_lookup: Callable[[], str | None] | None,
    role: str,
    archive_path_exists: Callable[[str], bool] | None,
) -> dict[str, str]:
    """Assemble template fields for the housekeeping hop branch."""
    try:
        journal_fields = hop_journal_projection_fields(
            journal_text=journal_text,
            register_text=register_text,
            read_uri=read_cortex_uri,
            read_journal_head=read_journal_head,
            archive_path_exists=archive_path_exists,
            role=role,
            lane=lane,
            inflight_projection_lookup=inflight_projection_lookup,
        )
    except Exception as exc:  # noqa: BLE001 — fail-open; one block, not the hop
        journal_fields = {
            "lessons_in_force": (
                "## Lessons in force\n\n"
                + _render_failed("lessons_in_force", exc, MAESTRO_JOURNAL_URI)
                + "\n"
            ),
            "register_card": (
                "## Register\n\n"
                + _render_failed("register_card", exc, MAESTRO_REGISTER_URI)
                + "\n"
            ),
        }

    rules_bytes = (
        len((house_rules_verbatim or "").encode("utf-8"))
        if house_rules_verbatim
        else None
    )
    checkpoint_bytes = (
        measure_checkpoint_budget_bytes(sidecar, inline_house_rules=inline_rules)
        if sidecar and not handoff_failure
        else None
    )
    runbook_bytes: int | None = None
    try:
        runbook_body, _runbook_err = load_maestro_runbook()
        runbook_bytes = runbook_refuse_steps_bytes(runbook_body)
    except Exception:  # noqa: BLE001 — budgets table is fail-open
        runbook_bytes = None
    raw_journal_bytes = journal_fields.get("journal_head_bytes")
    journal_head_bytes = int(raw_journal_bytes) if raw_journal_bytes else None
    try:
        budgets_table = render_budgets_table_block(
            lane=lane,
            checkpoint_capped_bytes=checkpoint_bytes,
            house_rules_bytes=rules_bytes,
            runbook_bytes=runbook_bytes,
            journal_head_bytes=journal_head_bytes,
        )
        over_budget = journal_fields.get("journal_over_budget_flag") or ""
        if over_budget:
            budgets_table = f"{budgets_table}\n\n{over_budget}"
    except Exception as exc:  # noqa: BLE001 — fail-open; one block, not the hop
        budgets_table = _render_failed(
            "budgets_table", exc, standing_handoff_uri(lane)
        )

    return {
        "hop_kind": hop_kind,
        "lane": lane,
        "hop_admitted_at_display": hop_admitted_at_display,
        "responsibility_display": responsibility_display,
        "successor_birth_id": birth,
        "execution_clause": execution_clause,
        "handoff_sha": digest,
        "running_now_line": running_now_line,
        "house_rules_verbatim": house_rules_verbatim,
        "checkpoint_block": checkpoint_block,
        "inflight_block": inflight_block,
        "lessons_in_force": journal_fields["lessons_in_force"],
        "register_card": journal_fields["register_card"],
        "live_leg": "",
        "pointers": "",
        "first_act_call1_line": "",
        "fetch_last": str(fetch_last),
        "budgets_table": budgets_table,
    }
