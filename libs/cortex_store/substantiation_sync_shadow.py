"""Read-only shadow diff — substantiation_sync retarget to ``confidence_band``.

Phase 3 cutover preview: same eligibility as ``recompute_entity_substantiation_status``
(via ``substantiation_sync_gating``). Compares stored ``confidence_band`` vs D-core
target; informational ``status`` column diff. No DB writes.
"""

from __future__ import annotations

import datetime
import sqlite3
from dataclasses import dataclass, field

from .confidence_field import confidence_field
from .db import query
from .status_trait_read import entity_has_trait_columns
from .substantiation_sync_gating import resolve_substantiation_sync_scope

_BANDS = ("unsubstantiated", "provisional", "confirmed")


@dataclass
class SubstantiationSyncShadowRow:
    entity_id: str
    entity_type: str
    status: str | None
    confidence_band: str | None
    derived_state: str
    target_band: str
    backing_assertion_count: int
    null_credibility_backing: int


@dataclass
class SubstantiationSyncShadowReport:
    generated_at: str
    db_path: str
    total_entities: int
    missing_trait_columns: bool
    skipped_missing_entity: int
    skipped_lifecycle_axis: int
    skipped_adoption_type: int
    skipped_non_status_confidence_field: int
    skipped_demotion_blocked: int
    in_scope: int
    would_change_confidence_band: int
    would_change_status_if_live_sync: int
    band_already_matches: int
    status_equals_target: int
    status_differs_from_band: int
    provisional_band_would_overwrite: int
    would_demote_band: int
    confusion_band: dict[str, dict[str, int]]
    null_credibility_entities_affected: int
    null_credibility_assertion_rows: int
    samples_band_change: list[SubstantiationSyncShadowRow] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def _empty_confusion() -> dict[str, dict[str, int]]:
    return {
        f"current_{b}": {f"target_{t}": 0 for t in _BANDS} for b in _BANDS + ("null",)
    }


def _backing_credibility_counts(
    conn: sqlite3.Connection, entity_id: str
) -> tuple[int, int]:
    """Non-superseded backing rows: (total, null_credibility)."""
    cols = {r[1] for r in conn.execute("PRAGMA table_info(assertions)").fetchall()}
    cred_col = ", credibility" if "credibility" in cols else ""
    rows = query(
        conn,
        f"SELECT confidence{cred_col} FROM assertions "
        "WHERE entity_id = ? AND superseded_by IS NULL",
        (entity_id,),
    )
    total = len(rows)
    if "credibility" not in cols:
        return total, total
    null_n = sum(1 for r in rows if r.get("credibility") is None)
    return total, null_n


def run_substantiation_sync_shadow(
    conn: sqlite3.Connection, *, db_path: str = ""
) -> SubstantiationSyncShadowReport:
    """Scan all entities; report retarget-to-``confidence_band`` deltas."""
    if not entity_has_trait_columns(conn):
        return SubstantiationSyncShadowReport(
            generated_at=datetime.datetime.now(tz=datetime.UTC).strftime(
                "%Y-%m-%dT%H:%M:%SZ"
            ),
            db_path=db_path,
            total_entities=0,
            missing_trait_columns=True,
            skipped_missing_entity=0,
            skipped_lifecycle_axis=0,
            skipped_adoption_type=0,
            skipped_non_status_confidence_field=0,
            skipped_demotion_blocked=0,
            in_scope=0,
            would_change_confidence_band=0,
            would_change_status_if_live_sync=0,
            band_already_matches=0,
            status_equals_target=0,
            status_differs_from_band=0,
            provisional_band_would_overwrite=0,
            would_demote_band=0,
            confusion_band=_empty_confusion(),
            null_credibility_entities_affected=0,
            null_credibility_assertion_rows=0,
            notes=["confidence_band column missing — run migration 050 first"],
        )

    from .dispatch_ops._detectors.substantiation import derive_substantiation_state

    entities = query(conn, "SELECT id, type, status, confidence_band FROM entities")
    confusion = _empty_confusion()
    report = SubstantiationSyncShadowReport(
        generated_at=datetime.datetime.now(tz=datetime.UTC).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        ),
        db_path=db_path,
        total_entities=len(entities),
        missing_trait_columns=False,
        skipped_missing_entity=0,
        skipped_lifecycle_axis=0,
        skipped_adoption_type=0,
        skipped_non_status_confidence_field=0,
        skipped_demotion_blocked=0,
        in_scope=0,
        would_change_confidence_band=0,
        would_change_status_if_live_sync=0,
        band_already_matches=0,
        status_equals_target=0,
        status_differs_from_band=0,
        provisional_band_would_overwrite=0,
        would_demote_band=0,
        confusion_band=confusion,
        null_credibility_entities_affected=0,
        null_credibility_assertion_rows=0,
        notes=[
            "Retarget preview: D-core binary gate → confidence_band (not status).",
            "Live substantiation_sync writes confidence_band only; demotions fail-closed.",
            "NULL credibility on backing assertions does not alter D-core target "
            "(binary confirmed assertion count); reported for Φ* gate awareness.",
        ],
    )

    for row in entities:
        eid = row["id"]
        if confidence_field(conn, row.get("type") or "") != "status":
            report.skipped_non_status_confidence_field += 1
            continue

        scope = resolve_substantiation_sync_scope(conn, eid)
        if scope.skip_reason == "missing_entity":
            report.skipped_missing_entity += 1
            continue
        if scope.skip_reason == "lifecycle_axis":
            report.skipped_lifecycle_axis += 1
            continue
        if scope.skip_reason == "adoption_type":
            report.skipped_adoption_type += 1
            continue
        if scope.demotion_blocked:
            report.skipped_demotion_blocked += 1
            report.would_demote_band += 1
            continue

        target = scope.target_band
        assert target is not None
        report.in_scope += 1
        status = row.get("status")
        band = row.get("confidence_band")
        derived = derive_substantiation_state(conn, eid)
        backing_n, null_cred_n = _backing_credibility_counts(conn, eid)

        cur_key = f"current_{band}" if band in _BANDS else "current_null"
        tgt_key = f"target_{target}"
        confusion[cur_key][tgt_key] += 1

        if band != target:
            report.would_change_confidence_band += 1
            if band == "provisional":
                report.provisional_band_would_overwrite += 1
            if null_cred_n > 0:
                report.null_credibility_entities_affected += 1
                report.null_credibility_assertion_rows += null_cred_n
            if len(report.samples_band_change) < 25:
                report.samples_band_change.append(
                    SubstantiationSyncShadowRow(
                        entity_id=eid,
                        entity_type=row.get("type") or "",
                        status=status,
                        confidence_band=band,
                        derived_state=derived,
                        target_band=target,
                        backing_assertion_count=backing_n,
                        null_credibility_backing=null_cred_n,
                    )
                )
        else:
            report.band_already_matches += 1

        if status != target:
            report.would_change_status_if_live_sync += 1
        else:
            report.status_equals_target += 1
        if status != band and (
            status in {"unsubstantiated", "confirmed", "provisional"} or band in _BANDS
        ):
            report.status_differs_from_band += 1

    return report


def render_markdown(report: SubstantiationSyncShadowReport) -> str:
    """Human-readable shadow report for operator / agent-bus."""
    lines = [
        "# Substantiation-sync shadow diff — Phase 3 retarget preview",
        f"- generated: {report.generated_at} · db: `{report.db_path}`",
        f"- total entities: {report.total_entities}",
        "",
        "## Scope",
        f"- in-scope (status-axis sync eligible): {report.in_scope}",
        f"- skipped lifecycle-axis: {report.skipped_lifecycle_axis}",
        f"- skipped adoption-type (decision): {report.skipped_adoption_type}",
        f"- skipped non-status confidence_field: "
        f"{report.skipped_non_status_confidence_field}",
        f"- skipped demotion-blocked (fail-closed): {report.skipped_demotion_blocked}",
        "",
        "## Retarget writes (confidence_band)",
        f"- **would change confidence_band**: {report.would_change_confidence_band}",
        f"- band already matches target: {report.band_already_matches}",
        f"- provisional band would be overwritten: "
        f"{report.provisional_band_would_overwrite}",
        f"- **would demote band** (blocked in production): {report.would_demote_band}",
        "",
        "## Live sync axis (status) — informational",
        f"- would change status if legacy status sync ran: "
        f"{report.would_change_status_if_live_sync}",
        f"- status already equals target: {report.status_equals_target}",
        f"- status ≠ confidence_band (in-scope): {report.status_differs_from_band}",
        "",
        "## Confusion (stored confidence_band ↓ vs D-core target →)",
        "| current \\ target | unsubstantiated | provisional | confirmed |",
        "|---|---:|---:|---:|",
    ]
    for b in _BANDS:
        key = f"current_{b}"
        if key not in report.confusion_band:
            continue
        row = report.confusion_band[key]
        lines.append(
            f"| {b} | {row.get('target_unsubstantiated', 0)} | "
            f"{row.get('target_provisional', 0)} | "
            f"{row.get('target_confirmed', 0)} |"
        )
    null_row = report.confusion_band.get("current_null", {})
    lines.append(
        f"| (null) | {null_row.get('target_unsubstantiated', 0)} | "
        f"{null_row.get('target_provisional', 0)} | "
        f"{null_row.get('target_confirmed', 0)} |"
    )
    lines += [
        "",
        "## NULL credibility (backing assertions, entities that would change band)",
        f"- affected entities: {report.null_credibility_entities_affected}",
        f"- assertion rows with NULL credibility: "
        f"{report.null_credibility_assertion_rows}",
        "",
        "## Notes",
        *[f"- {n}" for n in report.notes],
    ]
    if report.samples_band_change:
        lines += ["", "## Sample band changes (up to 25)"]
        for s in report.samples_band_change:
            lines.append(
                f"- `{s.entity_id}` ({s.entity_type}) band={s.confidence_band!r} "
                f"→ {s.target_band!r} status={s.status!r} derived={s.derived_state} "
                f"backing={s.backing_assertion_count} null_cred={s.null_credibility_backing}"
            )
    if report.missing_trait_columns:
        lines.insert(2, "- **BLOCKED**: trait columns missing")
    return "\n".join(lines)


__all__ = [
    "SubstantiationSyncShadowReport",
    "SubstantiationSyncShadowRow",
    "render_markdown",
    "run_substantiation_sync_shadow",
]
