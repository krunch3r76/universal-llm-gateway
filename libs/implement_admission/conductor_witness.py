"""Witnessed DONE fold — scoreboard Status is a projection, not self-authored.

The fold renders each row's Status from hung witnesses. Which rows exist is
read from the tip's own ``## Gated deliverables`` table first: a mission born
with acceptance rows (R1…Rn) must be folded against those rows, and the Card
projection the fold used to ask the work item for them carries no
``attributes`` (``cortex_store.card.get_entity_card``), which folded every
R-row mission as the G-ladder and left its R-BIND witnesses unread (worker
13713, 2026-10-01). The work item (full projection) is the fallback, the
G-ladder the last resort.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from implement_admission.closeout_helpers import cortex_files_root
from implement_admission.conductor_score_journal import (
    G_ROWS,
    forward_mutate_tip,
    read_tip,
    resolve_scoreboard_rows,
    tip_sha256,
)
from implement_admission.conductor_score_table import (
    STATUS_HEADER,
    header_indices,
    row_id_in,
    set_cell,
)
from implement_admission.conductor_witness_defaults import (
    DefaultWitnessCortex,
    DefaultWitnessGit,
    closeout_witnesses_for_slug,
)
from implement_admission.conductor_witness_table import (
    _G6_REVIEW_ARTIFACT_IDS,
    _artifact_map,
    _cdp_fail_route,
    _first_resolving_artifact,
    _g6_review_failure_reason,
    _gated_review_keys,
    _uri_resolves,
    row_witnesses,
)
from implement_admission.conductor_witness_types import (
    FoldDeps,
    FoldResult,
    Witness,
    WitnessBus,
    WitnessCortex,
    WitnessGit,
    WitnessNestedImplement,
    done_rows_claimed_in_closeout,
    row_status_in_tip,
    stops_block_reason,
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
    "WitnessNestedImplement",
    "closeout_witnesses_for_slug",
    "done_rows_claimed_in_closeout",
    "fold_scoreboard",
    "resolve_entry_gate_from_fold",
    "row_status_in_tip",
    "row_witnesses",
    "rows_in_tip",
]


def _missing_witness_message(
    row_id: str,
    *,
    stops: str | None = None,
    summon_mode: str | None = None,
) -> str:
    if stops:
        return f"stops: {stops}"
    if row_id == "G1":
        return "hang active derived_from todo→document:* (consult_kind=architecture)"
    if row_id == "G2":
        return "hang F1 or S7 frame URI on the tip"
    if row_id == "G3":
        return "hang S4b or S9 spec URI on the tip"
    if row_id == "G4":
        return (
            "hang a G4 verdict that clears G5 (URI whose body does not withhold/FAIL)"
        )
    if row_id == "G5":
        summon = (summon_mode or "").strip().lower().replace("-", "_")
        if summon == "attended":
            return (
                "check summoning_thread_id for an existing SCORE_RESURFACE after "
                "the G3 journal before posting (cite CDP exec id + review sha; "
                "a:37198); if one is there, fold read missed it — file friction, "
                "do not re-post"
            )
        return "nest implement commits or hang L1==HEAD (away G5; a:37198)"
    if row_id == "G6":
        return "hang R1 pre-land review URI (`cdp/opus-5` job=delivery-review on lane branch before merge)"
    if row_id == "G7":
        return "land L-sha on master after G6 review harvest"
    if row_id.startswith("R"):
        return f"hang {row_id}-BIND URI or {row_id}-LAND sha on the tip"
    return f"witness missing for {row_id}"


def rows_in_tip(tip_body: str) -> tuple[str, ...]:
    """Row ids the scoreboard tip itself declares, in table order.

    Only the ``## Gated deliverables`` table is read: the Sidecars table
    carries artifact ids such as ``R3-BIND`` that are not rows. Returns an
    empty tuple when the tip has no such table, so callers can fall back.
    """
    rows: list[str] = []
    in_gated = False
    for line in (tip_body or "").splitlines():
        if line.startswith("## Gated deliverables"):
            in_gated = True
            continue
        if line.startswith("## "):
            in_gated = False
        if not in_gated:
            continue
        row_id = row_id_in(line)
        if row_id and row_id not in rows:
            rows.append(row_id)
    return tuple(rows)


def _scoreboard_rows(
    slug: str,
    *,
    deps: FoldDeps,
    files_root: Path,
    tip_body: str = "",
) -> tuple[str, ...]:
    """Rows to fold: the tip's own table, else the work item, else the G-ladder.

    Tip rows are used only when every declared row is an R row. Any G row
    keeps ``G_ROWS`` so the ladder witnesses (G1 derived_from, G4 withhold/FAIL
    body, G6 verdict/sha, F1/S7, S4b/S9, L1) still apply — one R row the
    conductor added must not select the custom-row rules. ``row_id_in`` yields
    only ``G1``–``G7`` or ``R\\d+``, so a table of other custom ids is not a
    tip-row set and those ids are not folded. Only a tip with no G/R row table
    at all consults the work item, and then with the full
    projection the materializer used at birth (``conductor_materialize``),
    never the Card, which has no ``attributes``. A cortex read failure, a
    missing ``attributes`` map, or a non-dict entity degrades to ``G_ROWS``
    rather than failing the fold. A missing tip is the caller's problem:
    ``fold_scoreboard`` returns None before this runs.
    """
    _ = files_root
    from_tip = rows_in_tip(tip_body)
    if from_tip:
        if any(row_id.startswith("G") for row_id in from_tip):
            return G_ROWS
        return from_tip
    source_ref = deps.source_ref or f"todo:{slug}"
    attrs: dict[str, Any] = {}
    try:
        entity = deps.cortex.entity_get(source_ref, intent="full")
        raw_attrs = entity.get("attributes") if isinstance(entity, dict) else None
        attrs = raw_attrs if isinstance(raw_attrs, dict) else {}
    except Exception:  # noqa: BLE001 — fold is advisory; G-ladder is the floor
        attrs = {}
    return resolve_scoreboard_rows(attrs)


def _render_folded_body(
    body: str,
    row_status: dict[str, str],
    rows: tuple[str, ...],
) -> str:
    column = header_indices(body).get(STATUS_HEADER)
    if column is None:
        # No Status column to write: a table we cannot address is left alone
        # rather than written at the legacy 4-column position (B0-2).
        return body
    wanted = {row_id.upper(): row_id for row_id in rows}
    out: list[str] = []
    in_gated = False
    for line in body.splitlines():
        if line.startswith("## Gated deliverables"):
            in_gated = True
        elif line.startswith("## "):
            in_gated = False
        if in_gated:
            row_id = wanted.get(row_id_in(line) or "")
            status = row_status.get(row_id) if row_id else None
            if status:
                line = set_cell(line, column, status)
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
    rows = _scoreboard_rows(slug, deps=deps, files_root=root, tip_body=raw_body)
    witnesses = row_witnesses(
        slug,
        tip_body=raw_body,
        deps=deps,
        files_root=root,
        rows=rows,
    )
    witnessed_done = frozenset(
        row_id for row_id, w in witnesses.items() if w is not None
    )
    row_status: dict[str, str] = {}
    rows_claimed: set[str] = set()
    missing: dict[str, str] = {}
    blocked: dict[str, str] = {}
    for row_id in rows:
        raw_status = (row_status_in_tip(raw_body, row_id) or "OPEN").upper()
        stops = stops_block_reason(raw_body, row_id) if row_id == "G4" else None
        if witnesses.get(row_id) is not None:
            row_status[row_id] = "DONE"
        elif raw_status in {"DONE", "CLAIMED"}:
            row_status[row_id] = "CLAIMED"
            rows_claimed.add(row_id)
            missing[row_id] = _missing_witness_message(
                row_id,
                stops=stops,
                summon_mode=deps.summon_mode,
            )
        elif stops:
            blocked[row_id] = stops
            row_status[row_id] = raw_status if raw_status != "DONE" else "OPEN"
        else:
            row_status[row_id] = raw_status if raw_status != "DONE" else "OPEN"

    if witnesses.get("G6") is None and "G6" in rows:
        artifacts = _artifact_map(raw_body)
        g6_route = _cdp_fail_route(raw_body, "G6")
        g6_id, g6_uri = _first_resolving_artifact(
            artifacts,
            _gated_review_keys("G6", _G6_REVIEW_ARTIFACT_IDS, g6_route),
            files_root=root,
            repo=deps.repo,
            route=g6_route,
        )
        if g6_id and g6_uri and _uri_resolves(g6_uri, files_root=root, repo=deps.repo):
            block = _g6_review_failure_reason(
                g6_uri,
                files_root=root,
                tip_body=raw_body,
                artifact_id=g6_id,
            )
            if block:
                missing["G6"] = block

    folded_body = _render_folded_body(raw_body, row_status, rows)
    sources = {row_id: w.source for row_id, w in witnesses.items() if w is not None}
    journal_applied = False
    if folded_body != raw_body and write_journal:
        delta_parts: list[str] = []
        for row_id in rows:
            prior = row_status_in_tip(raw_body, row_id) or "OPEN"
            if prior == row_status[row_id]:
                continue
            part = f"{row_id} {prior}→{row_status[row_id]}"
            stops = stops_block_reason(raw_body, row_id) if row_id == "G4" else None
            if stops:
                part = f"{part} [stops: {stops}]"
            source = sources.get(row_id) or ""
            marker = ":route="
            if marker in source:
                part = f"{part} [route={source.rsplit(marker, 1)[1]}]"
            delta_parts.append(part)
        delta = " ".join(delta_parts) or "witness fold render"
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
            delta=delta,
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
        blocked_rows=blocked,
        journal_applied=journal_applied,
        tip_sha=tip_sha256(folded_body),
    )
