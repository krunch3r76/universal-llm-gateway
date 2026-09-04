"""Witnessed DONE fold — scoreboard Status is a projection, not self-authored."""

from __future__ import annotations

from pathlib import Path

from implement_admission.closeout_helpers import cortex_files_root
from implement_admission.conductor_score_journal import (
    G_ROWS,
    forward_mutate_tip,
    read_tip,
    resolve_scoreboard_rows,
    tip_sha256,
)
from implement_admission.conductor_witness_defaults import (
    DefaultWitnessCortex,
    DefaultWitnessGit,
    closeout_witnesses_for_slug,
)
from implement_admission.conductor_witness_table import row_witnesses
from implement_admission.conductor_witness_types import (
    FoldDeps,
    FoldResult,
    Witness,
    WitnessBus,
    WitnessCortex,
    WitnessGit,
    done_rows_claimed_in_closeout,
    row_status_in_tip,
)
from implement_admission.events_conductor_witness import (
    emit_conductor_score_witness_fold,
)

__all__ = [
    "DefaultWitnessCortex",
    "DefaultWitnessGit",
    "FoldDeps",
    "FoldResult",
    "Witness",
    "WitnessBus",
    "WitnessCortex",
    "WitnessGit",
    "closeout_witnesses_for_slug",
    "done_rows_claimed_in_closeout",
    "fold_scoreboard",
    "resolve_entry_gate_from_fold",
    "row_status_in_tip",
    "row_witnesses",
]


def _missing_witness_message(row_id: str) -> str:
    if row_id == "G1":
        return "hang active derived_from todo→document:* (consult_kind=architecture)"
    if row_id == "G2":
        return "hang F1 or S7 frame URI on the tip"
    if row_id == "G3":
        return "hang S4b or S9 spec URI on the tip"
    if row_id == "G4":
        return "hang a G4 verdict that clears G5 (URI whose body does not withhold/FAIL)"
    if row_id == "G5":
        return "post SCORE_RESURFACE on summoning thread after G3 journal"
    if row_id == "G6":
        return "hang R1 pre-land review URI (`cdp/opus-5` purpose=review on lane branch before merge)"
    if row_id == "G7":
        return "land L-sha on master after G6 review harvest"
    if row_id.startswith("R"):
        return f"hang {row_id}-BIND URI or {row_id}-LAND sha on the tip"
    return f"witness missing for {row_id}"


def _scoreboard_rows(
    slug: str,
    *,
    deps: FoldDeps,
    files_root: Path,
) -> tuple[str, ...]:
    source_ref = deps.source_ref or f"todo:{slug}"
    entity = deps.cortex.entity_get(source_ref, intent="card")
    attrs = entity.get("attributes") or {}
    return resolve_scoreboard_rows(attrs)


def _render_folded_body(
    body: str,
    row_status: dict[str, str],
    rows: tuple[str, ...],
) -> str:
    lines = body.splitlines()
    out: list[str] = []
    in_gated = False
    for line in lines:
        if line.startswith("## Gated deliverables"):
            in_gated = True
        elif line.startswith("## "):
            in_gated = False
        if in_gated:
            for row_id in rows:
                prefix = f"| {row_id} |"
                if line.startswith(prefix) or line.startswith(f"| {row_id.lower()} |"):
                    status = row_status.get(row_id)
                    if status:
                        parts = line.split("|")
                        if len(parts) >= 4:
                            parts[3] = f" {status} "
                            line = "|".join(parts)
                    break
        out.append(line)
    return "\n".join(out) + ("\n" if body.endswith("\n") else "")


def resolve_entry_gate_from_fold(fold: FoldResult) -> str:
    """First scoreboard row whose folded status is not DONE."""
    rows = tuple(fold.row_status.keys())
    for row_id in rows:
        if fold.row_status.get(row_id) != "DONE":
            return row_id
    return rows[-1]


def fold_scoreboard(
    slug: str,
    *,
    deps: FoldDeps,
    files_root: Path | None = None,
    write_journal: bool = True,
) -> FoldResult | None:
    """Render tip Status from witnesses; journal the fold when the body changes."""
    root = files_root if files_root is not None else cortex_files_root()
    prior = read_tip(slug, files_root=root)
    if prior is None:
        return None
    raw_body = prior[0]
    rows = _scoreboard_rows(slug, deps=deps, files_root=root)
    witnesses = row_witnesses(
        slug,
        tip_body=raw_body,
        deps=deps,
        files_root=root,
        rows=rows,
    )
    witnessed_done = frozenset(row_id for row_id, w in witnesses.items() if w is not None)
    row_status: dict[str, str] = {}
    rows_claimed: set[str] = set()
    missing: dict[str, str] = {}
    for row_id in rows:
        raw_status = (row_status_in_tip(raw_body, row_id) or "OPEN").upper()
        if witnesses.get(row_id) is not None:
            row_status[row_id] = "DONE"
        elif raw_status in {"DONE", "CLAIMED"}:
            row_status[row_id] = "CLAIMED"
            rows_claimed.add(row_id)
            missing[row_id] = _missing_witness_message(row_id)
        else:
            row_status[row_id] = raw_status if raw_status != "DONE" else "OPEN"

    folded_body = _render_folded_body(raw_body, row_status, rows)
    sources = {row_id: w.source for row_id, w in witnesses.items() if w is not None}
    journal_applied = False
    if folded_body != raw_body and write_journal:
        delta = " ".join(
            f"{row_id} {row_status_in_tip(raw_body, row_id) or 'OPEN'}→{row_status[row_id]}"
            for row_id in rows
            if (row_status_in_tip(raw_body, row_id) or "OPEN") != row_status[row_id]
        )
        result = forward_mutate_tip(
            slug,
            next_body=folded_body,
            seat="fold",
            dispatch_id=None,
            reason="witness_fold",
            rows=tuple(
                row_id
                for row_id in rows
                if row_status[row_id] != (row_status_in_tip(raw_body, row_id) or "OPEN")
            ),
            delta=delta or "witness fold render",
            files_root=root,
            prior_witnessed_done=witnessed_done,
        )
        journal_applied = result.rejected_reason is None
        emit_conductor_score_witness_fold(
            slug=slug,
            rows_done=tuple(sorted(witnessed_done)),
            rows_claimed=tuple(sorted(rows_claimed)),
            sources=sources,
        )

    return FoldResult(
        slug=slug,
        raw_body=raw_body,
        folded_body=folded_body,
        row_status=row_status,
        witnesses=witnesses,
        witnessed_done=witnessed_done,
        rows_claimed=frozenset(rows_claimed),
        entry_gate=next(
            (row_id for row_id in rows if row_status.get(row_id) != "DONE"),
            rows[-1],
        ),
        missing_witnesses=missing,
        journal_applied=journal_applied,
        tip_sha=tip_sha256(folded_body),
    )
