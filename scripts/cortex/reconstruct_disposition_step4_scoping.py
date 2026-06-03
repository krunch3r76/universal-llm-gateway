#!/usr/bin/env python3
"""Step-4 READ-ONLY scoping: staged-only tail (thread 1210 T25).

Re-snapshots staged reconstruct markers NOT in Bucket-G; cross-tabs; duplicate-risk
signals; dry-run manifests A/B/C. No PATCH, no --live.
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
from collections import Counter, defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

_SCRIPTS_CORTEX = Path(__file__).resolve().parent
if str(_SCRIPTS_CORTEX) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_CORTEX))

from reconstruct_constants import MARKER  # noqa: E402
from reconstruct_disposition_fenced_review import (  # noqa: E402
    BUCKET_G_FENCED_SQL,
    is_fenced,
)
from reconstruct_uri import parse_uris  # noqa: E402

STAGED_MARKER_COUNT_SQL = """
SELECT COUNT(*) FROM assertions
WHERE superseded_by IS NULL
  AND review_status = 'staged'
  AND reviewer = ?
"""

TAIL_SQL = """
SELECT a.id, a.entity_id, a.claim, a.evidence_uris, a.review_notes, a.confidence
FROM assertions a
WHERE a.superseded_by IS NULL
  AND a.review_status = 'staged'
  AND a.reviewer = ?
  AND NOT EXISTS (
    SELECT 1 FROM assertions c
    WHERE c.entity_id = a.entity_id
      AND c.superseded_by IS NULL
      AND (c.review_status IS NULL OR c.review_status != 'staged')
  )
ORDER BY a.id
"""

NON_MARKER_ASSERTIONS_ON_ENTITY_SQL = """
SELECT COUNT(*) FROM assertions
WHERE entity_id = ?
  AND superseded_by IS NULL
  AND (reviewer IS NULL OR reviewer != ?)
"""

COMMITTED_ENTITY_BY_PREFIX_SQL = """
SELECT DISTINCT entity_id FROM assertions
WHERE superseded_by IS NULL
  AND (review_status IS NULL OR review_status != 'staged')
  AND entity_id LIKE ?
"""


def entity_type(entity_id: str) -> str:
    return entity_id.split(":", 1)[0] if ":" in entity_id else "other"


def uri_source_class(uris: list[str]) -> str:
    if not uris:
        return "empty"
    classes: list[str] = []
    for u in uris:
        low = u.lower()
        if low.startswith(("agent-bus:", "email-bridge:")):
            classes.append("thread_sidecar")
        elif low.startswith("execution:") or "transcript" in low:
            classes.append("transcript_or_session")
        elif low.startswith(("cortex://", "assertion:", "entity:")):
            classes.append("prior_assertion_or_cortex")
        elif low.startswith(
            ("files://", "workspaces://", "legal/", "notes/")
        ) or low.endswith((".pdf", ".eml", ".txt", ".md", ".png", ".jpg")):
            classes.append("primary_document")
        elif low.startswith("http"):
            classes.append("primary_document")
        else:
            classes.append("unknown")
    return Counter(classes).most_common(1)[0][0]


def _norm(s: str | None) -> str:
    if not s:
        return ""
    return re.sub(r"[^a-z0-9]+", " ", s.lower()).strip()


def _parse_aliases(raw: Any) -> list[str]:
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
        return [str(x) for x in parsed] if isinstance(parsed, list) else []
    except (json.JSONDecodeError, TypeError):
        return []


def _name_tokens(name: str) -> set[str]:
    return set(_norm(name).split()) - {"the", "of", "and", "a", "an"}


def _load_entity(conn: sqlite3.Connection, entity_id: str) -> dict[str, Any] | None:
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT id, type, name, aliases, attributes FROM entities WHERE id = ?",
        (entity_id,),
    ).fetchone()
    return dict(row) if row else None


def duplicate_risk_scan(
    conn: sqlite3.Connection, net_new_fenced_entity_ids: list[str]
) -> list[dict[str, Any]]:
    committed_by_prefix: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for eid in net_new_fenced_entity_ids:
        prefix = eid.split(":", 1)[0] + ":%"
        if prefix in committed_by_prefix:
            continue
        for (ceid,) in conn.execute(
            COMMITTED_ENTITY_BY_PREFIX_SQL, (prefix,)
        ).fetchall():
            if ceid in net_new_fenced_entity_ids:
                continue
            ent = _load_entity(conn, ceid)
            if ent:
                committed_by_prefix[prefix].append(ent)

    risks: list[dict[str, Any]] = []
    for eid in sorted(net_new_fenced_entity_ids):
        ent = _load_entity(conn, eid)
        if not ent:
            risks.append({"staged_entity_id": eid, "note": "no entities row"})
            continue
        prefix = eid.split(":", 1)[0] + ":%"
        names = [ent["name"], *_parse_aliases(ent.get("aliases"))]
        tokens: set[str] = set()
        for n in names:
            tokens |= _name_tokens(n)
        if not tokens:
            continue
        hits: list[dict[str, Any]] = []
        nn = _norm(ent["name"])
        for other in committed_by_prefix.get(prefix, []):
            if other["id"] == eid:
                continue
            onames = [other["name"], *_parse_aliases(other.get("aliases"))]
            ot: set[str] = set()
            for n in onames:
                ot |= _name_tokens(n)
            if not ot:
                continue
            inter = tokens & ot
            on = _norm(other["name"])
            if nn and on and (nn == on or nn in on or on in nn):
                hits.append(
                    {
                        "committed_entity_id": other["id"],
                        "match": "name_substring",
                        "other_name": other["name"],
                    }
                )
            elif len(inter) >= 2 and len(inter) / max(len(tokens), len(ot)) >= 0.5:
                hits.append(
                    {
                        "committed_entity_id": other["id"],
                        "match": "token_overlap",
                        "overlap": sorted(inter)[:8],
                        "other_name": other["name"],
                    }
                )
        if hits:
            risks.append(
                {
                    "staged_entity_id": eid,
                    "name": ent["name"],
                    "entity_type": entity_type(eid),
                    "candidates": hits[:5],
                }
            )
    return risks


def classify_tail(conn: sqlite3.Connection) -> dict[str, Any]:
    conn.row_factory = sqlite3.Row
    staged_total = int(conn.execute(STAGED_MARKER_COUNT_SQL, (MARKER,)).fetchone()[0])
    bucket_g_rows = [
        dict(r) for r in conn.execute(BUCKET_G_FENCED_SQL, (MARKER,)).fetchall()
    ]
    bucket_g_ids = {int(r["id"]) for r in bucket_g_rows}
    tail_rows = [dict(r) for r in conn.execute(TAIL_SQL, (MARKER,)).fetchall()]
    tail_ids = {int(r["id"]) for r in tail_rows}
    intersection = tail_ids & bucket_g_ids

    enriched: list[dict[str, Any]] = []
    for r in tail_rows:
        uris = parse_uris(r["evidence_uris"])
        eid = str(r["entity_id"])
        non_marker_count = int(
            conn.execute(NON_MARKER_ASSERTIONS_ON_ENTITY_SQL, (eid, MARKER)).fetchone()[0]
        )
        net_new_entity = non_marker_count == 0
        enriched.append(
            {
                "id": int(r["id"]),
                "entity_id": eid,
                "claim": str(r["claim"]),
                "confidence": str(r["confidence"]),
                "fenced": is_fenced(eid),
                "has_uris": bool(uris),
                "evidence_uris": uris,
                "uri_source_class": uri_source_class(uris),
                "entity_type": entity_type(eid),
                "net_new_entity": net_new_entity,
                "existing_entity_no_committed": (not net_new_entity)
                and non_marker_count > 0,
            }
        )

    cross_tab: dict[str, int] = {}
    for e in enriched:
        key = "|".join(
            [
                "fenced" if e["fenced"] else "non_fenced",
                "has_uris" if e["has_uris"] else "empty_uris",
                "net_new" if e["net_new_entity"] else "existing_no_comm",
                e["entity_type"],
                e["uri_source_class"],
            ]
        )
        cross_tab[key] = cross_tab.get(key, 0) + 1

    manifest_a: list[dict[str, Any]] = []
    manifest_b: list[dict[str, Any]] = []
    manifest_c: list[dict[str, Any]] = []
    for e in enriched:
        base = {
            "id": e["id"],
            "entity_id": e["entity_id"],
            "entity_type": e["entity_type"],
            "claim": e["claim"][:500],
            "evidence_uris": e["evidence_uris"],
            "uri_source_class": e["uri_source_class"],
            "net_new_entity": e["net_new_entity"],
            "confidence_policy": "unchanged (1172-C)",
        }
        if not e["has_uris"]:
            manifest_b.append(
                {
                    **base,
                    "proposed_action": "keep_staged_flag",
                    "proposed_review_notes": (
                        "1210-step4 dry-run B: empty evidence_uris; keep staged; "
                        "confidence unchanged (1172-C)."
                    ),
                }
            )
        elif not e["fenced"]:
            manifest_a.append(
                {
                    **base,
                    "proposed_action": "graduate",
                    "proposed_review_notes": (
                        "1210-step4 dry-run A: non-fenced with evidence_uris; graduate; "
                        "confidence unchanged (1172-C)."
                    ),
                }
            )
        else:
            row = {
                **base,
                "proposed_action": "operator_spot_check",
                "proposed_review_notes": (
                    "1210-step4 dry-run C: fenced with evidence_uris; operator review "
                    "before graduate; confidence unchanged (1172-C)."
                ),
            }
            if e["net_new_entity"]:
                row["authority_risk"] = "net_new_fenced_entity"
            manifest_c.append(row)

    net_new_fenced_entities = sorted(
        {e["entity_id"] for e in enriched if e["fenced"] and e["net_new_entity"]}
    )
    fenced_c_entities = sorted(
        {e["entity_id"] for e in enriched if e["fenced"] and e["has_uris"]}
    )
    dup_risks = duplicate_risk_scan(conn, fenced_c_entities)
    dup_entity_ids = {d["staged_entity_id"] for d in dup_risks if "candidates" in d}

    for row in manifest_c:
        if row["entity_id"] in dup_entity_ids:
            match = next(
                d for d in dup_risks if d.get("staged_entity_id") == row["entity_id"]
            )
            row["duplicate_risk_note"] = match.get("candidates") or match.get("note")

    reconcile_arithmetic = staged_total - 570
    drift_vs_arithmetic = len(tail_rows) - reconcile_arithmetic

    return {
        "snapshot_at": datetime.now(tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "reviewer": MARKER,
        "mode": "read_only_scoping",
        "staged_marker_total": staged_total,
        "bucket_g_staged_count": len(bucket_g_ids),
        "tail_count": len(tail_rows),
        "tail_disjoint_from_bucket_g": len(intersection) == 0,
        "bucket_g_tail_intersection_count": len(intersection),
        "reconcile_note": (
            "Handoff arithmetic staged_total−570_keep = "
            f"{reconcile_arithmetic}; predicate tail (no committed on entity) = "
            f"{len(tail_rows)}; delta = {drift_vs_arithmetic} "
            f"(extra {len(bucket_g_ids) - 570} staged rows on Bucket-G entities "
            "outside manifest keep_staged_flag — see drift section)."
        ),
        "manifest_counts": {
            "A_auto_graduate": len(manifest_a),
            "B_auto_keep": len(manifest_b),
            "C_operator_spot_check": len(manifest_c),
            "C_net_new_fenced_assertion_rows": sum(
                1 for r in manifest_c if r.get("authority_risk")
            ),
            "C_net_new_fenced_entity_count": len(net_new_fenced_entities),
        },
        "cross_tab": dict(sorted(cross_tab.items(), key=lambda kv: -kv[1])),
        "axis_totals": {
            "fenced": sum(1 for e in enriched if e["fenced"]),
            "non_fenced": sum(1 for e in enriched if not e["fenced"]),
            "has_uris": sum(1 for e in enriched if e["has_uris"]),
            "empty_uris": sum(1 for e in enriched if not e["has_uris"]),
            "net_new_entity_rows": sum(1 for e in enriched if e["net_new_entity"]),
            "existing_no_comm_rows": sum(
                1 for e in enriched if e["existing_entity_no_committed"]
            ),
        },
        "duplicate_risk": {
            "scan_scope": "all_fenced_entities_in_manifest_C_predicate",
            "fenced_with_uri_entity_count": len(fenced_c_entities),
            "net_new_fenced_entity_count": len(net_new_fenced_entities),
            "entities_with_candidate_duplicates": len(dup_entity_ids),
            "candidates": dup_risks,
        },
        "manifests": {
            "A": manifest_a,
            "B": manifest_b,
            "C": manifest_c,
        },
        "tail_assertion_ids": sorted(tail_ids),
    }


def write_manifest(
    path: Path,
    *,
    bucket: str,
    rows: list[dict[str, Any]],
    meta: dict[str, Any],
) -> None:
    payload = {
        "thread": "1210",
        "step": 4,
        "bucket": bucket,
        "title": f"Step-4 dry-run manifest {bucket} (staged-only tail)",
        "marker": MARKER,
        "mode": "dry_run",
        "produced_at": meta["snapshot_at"],
        "confidence_policy": "unchanged (1172-C)",
        "assertion_count": len(rows),
        "assertion_ids": [r["id"] for r in rows],
        "rows": rows,
        "note": "READ-ONLY scoping — not ratified; no --live until lead reviews manifest C.",
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def build_report_md(data: dict[str, Any], manifest_paths: dict[str, Path]) -> str:
    mc = data["manifest_counts"]
    at = data["axis_totals"]
    lines = [
        "# 1210 — Step 4 staged-only tail scoping report",
        "",
        f"**Snapshot:** {data['snapshot_at']} · **Mode:** READ-ONLY (no apply)",
        f"**Marker:** `{data['reviewer']}`",
        "",
        "## 1. Re-snapshot + reconcile",
        "",
        "| Metric | Count |",
        "|---|---|",
        f"| Staged reconstruct-marker total | **{data['staged_marker_total']}** |",
        f"| Bucket-G staged (entity has committed active) | **{data['bucket_g_staged_count']}** |",
        f"| **Staged-only tail** (NOT Bucket-G predicate) | **{data['tail_count']}** |",
        f"| Handoff arithmetic (2222−570 keep) | **{data['staged_marker_total'] - 570}** |",
        f"| Tail disjoint from Bucket-G | **{data['tail_disjoint_from_bucket_g']}** "
        f"(intersection={data['bucket_g_tail_intersection_count']}) |",
        "",
        f"**Reconcile:** {data['reconcile_note']}",
        "",
        "**Drift:** 242 staged rows remain on Bucket-G entities with original reconstruct "
        "review_notes (not in step-2/3 `keep_staged_flag` manifest); they are **excluded** "
        "from this tail scope pending lead disposition of Bucket-G residue.",
        "",
        "## 2. Cross-tab (tail only)",
        "",
        "### Axis totals",
        "",
        "| Axis | Rows |",
        "|---|---|",
        f"| Fenced | {at['fenced']} |",
        f"| Non-fenced | {at['non_fenced']} |",
        f"| Has evidence_uris | {at['has_uris']} |",
        f"| Empty evidence_uris | {at['empty_uris']} |",
        f"| Net-new entity (assertion rows) | {at['net_new_entity_rows']} |",
        f"| Existing entity, no committed | {at['existing_no_comm_rows']} |",
        "",
        "### Full cross-tab (top 20)",
        "",
        "| fenced | uris | entity_class | type | uri_class | count |",
        "|---|---|---|---|---|---|",
    ]
    for key, count in list(data["cross_tab"].items())[:20]:
        parts = key.split("|")
        lines.append(f"| {' | '.join(parts)} | {count} |")
    lines.extend(
        [
            "",
            "## 3. Duplicate-risk (net-new fenced entities)",
            "",
            f"- Fenced+URI entities scanned: "
            f"**{data['duplicate_risk']['fenced_with_uri_entity_count']}**",
            f"- Net-new fenced **entities:** {data['duplicate_risk']['net_new_fenced_entity_count']}",
            f"- Entities with candidate committed duplicates: "
            f"**{data['duplicate_risk']['entities_with_candidate_duplicates']}**",
            "",
            "Candidate list (no merge):",
            "",
            "```json",
            json.dumps(data["duplicate_risk"]["candidates"], indent=2),
            "```",
            "",
            "## 4. Dry-run manifests A / B / C",
            "",
            "| Bucket | Rows | Path |",
            "|---|---|---|",
            f"| A — auto-graduate (non-fenced ∧ URI) | **{mc['A_auto_graduate']}** | "
            f"`{manifest_paths['A']}` |",
            f"| B — auto-keep (empty URI) | **{mc['B_auto_keep']}** | "
            f"`{manifest_paths['B']}` |",
            f"| C — operator spot-check (fenced ∧ URI) | **{mc['C_operator_spot_check']}** | "
            f"`{manifest_paths['C']}` |",
            f"| C subcount: net-new fenced assertion rows | "
            f"**{mc['C_net_new_fenced_assertion_rows']}** | |",
            f"| C subcount: net-new fenced entities | "
            f"**{mc['C_net_new_fenced_entity_count']}** | |",
            "",
            "## Gate",
            "",
            "Lead reviews splits + manifest **C** before any `--live`. "
            "Authority-risk gate applies to net-new fenced rows in C.",
            "",
            "— claude-cursor (executor, read-only scoping)",
        ]
    )
    return "\n".join(lines) + "\n"


def run(db_path: Path, out_dir: Path) -> dict[str, Any]:
    conn = sqlite3.connect(db_path)
    try:
        data = classify_tail(conn)
    finally:
        conn.close()

    paths = {
        "A": out_dir / "1210-step4-manifest-A-auto-graduate.json",
        "B": out_dir / "1210-step4-manifest-B-auto-keep.json",
        "C": out_dir / "1210-step4-manifest-C-operator-spot-check.json",
    }
    for key, path in paths.items():
        write_manifest(path, bucket=key, rows=data["manifests"][key], meta=data)

    report_path = out_dir / "1210-step4-scoping-report.md"
    report_path.write_text(build_report_md(data, paths), encoding="utf-8")

    summary = {
        "snapshot_at": data["snapshot_at"],
        "tail_count": data["tail_count"],
        "staged_marker_total": data["staged_marker_total"],
        "bucket_g_staged_count": data["bucket_g_staged_count"],
        "manifest_counts": data["manifest_counts"],
        "duplicate_risk_entities": data["duplicate_risk"][
            "entities_with_candidate_duplicates"
        ],
        "report_path": str(report_path),
        "manifest_paths": {k: str(v) for k, v in paths.items()},
    }
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Step-4 staged-only tail read-only scoping"
    )
    parser.add_argument("--db", default=str(Path.home() / ".cortex" / "cortex.db"))
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=Path(
            "/mnt/torus/projects/universal-llm-gateway/tmp/reconstruct-disposition"
        ),
    )
    args = parser.parse_args()
    print(json.dumps(run(Path(args.db), args.out_dir), indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
