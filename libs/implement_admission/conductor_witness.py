"""Witnessed DONE fold — scoreboard Status is a projection, not self-authored."""

from __future__ import annotations

from pathlib import Path

from implement_admission.closeout_helpers import cortex_files_root
from implement_admission.conductor_score_journal import (
    G_ROWS,
    forward_mutate_tip,
    read_tip,
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


def _missing_witness_message(gid: str) -> str:
    if gid == "G1":
        return "hang active derived_from todo→document:* (consult_kind=architecture)"
    if gid == "G4":
        return "hang G4 verdict artifact URI in tip table"
    if gid == "G5":
        return "post SCORE_RESURFACE on summoning thread after G3 journal"
    if gid == "G6":
        return "land L-sha on master"
    return f"witness missing for {gid}"


def _render_folded_body(body: str, row_status: dict[str, str]) -> str:
    lines = body.splitlines()
    out: list[str] = []
    for line in lines:
        for gid in G_ROWS:
            prefix = f"| {gid} |"
            if line.startswith(prefix) or line.startswith(f"| {gid.lower()} |"):
                status = row_status.get(gid)
                if status:
                    parts = line.split("|")
                    if len(parts) >= 4:
                        parts[3] = f" {status} "
                        line = "|".join(parts)
                break
        out.append(line)
    return "\n".join(out) + ("\n" if body.endswith("\n") else "")


def resolve_entry_gate_from_fold(fold: FoldResult) -> str:
    """First G-row whose folded status is not DONE."""
    for gid in G_ROWS:
        if fold.row_status.get(gid) != "DONE":
            return gid
    return G_ROWS[-1]


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
    witnesses = row_witnesses(slug, tip_body=raw_body, deps=deps, files_root=root)
    witnessed_done = frozenset(gid for gid, w in witnesses.items() if w is not None)
    row_status: dict[str, str] = {}
    rows_claimed: set[str] = set()
    missing: dict[str, str] = {}
    for gid in G_ROWS:
        if witnesses.get(gid) is not None:
            row_status[gid] = "DONE"
        elif (row_status_in_tip(raw_body, gid) or "") == "DONE":
            row_status[gid] = "CLAIMED"
            rows_claimed.add(gid)
            missing[gid] = _missing_witness_message(gid)
        else:
            raw = row_status_in_tip(raw_body, gid) or "OPEN"
            row_status[gid] = raw if raw != "DONE" else "OPEN"

    folded_body = _render_folded_body(raw_body, row_status)
    sources = {gid: w.source for gid, w in witnesses.items() if w is not None}
    journal_applied = False
    if folded_body != raw_body and write_journal:
        delta = " ".join(
            f"{gid} {row_status_in_tip(raw_body, gid) or 'OPEN'}→{row_status[gid]}"
            for gid in G_ROWS
            if (row_status_in_tip(raw_body, gid) or "OPEN") != row_status[gid]
        )
        result = forward_mutate_tip(
            slug,
            next_body=folded_body,
            seat="fold",
            dispatch_id=None,
            reason="witness_fold",
            rows=tuple(
                gid
                for gid in G_ROWS
                if row_status[gid] != (row_status_in_tip(raw_body, gid) or "OPEN")
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
            (gid for gid in G_ROWS if row_status.get(gid) != "DONE"),
            G_ROWS[-1],
        ),
        missing_witnesses=missing,
        journal_applied=journal_applied,
        tip_sha=tip_sha256(folded_body),
    )
