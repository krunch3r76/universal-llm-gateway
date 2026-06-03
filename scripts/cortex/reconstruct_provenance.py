#!/usr/bin/env python3
"""T13 provenance reconstruct — attach-or-flag pass (no auto-downgrade).

Reads candidates from cortex DB, attempts resolvable source location, then either:
  ATTACH — POST /assertions/supersede (confidence unchanged, seeded_by marker)
  FLAG   — PATCH review_status=staged (advisory metadata only)

**Disposition filter (CRITICAL — thread 1172 / handoff T19):** Before any batch
PATCH or supersede on *already staged* reconstruct flags, the SQL/API filter MUST
include ``reviewer='reconstruct-2026-06-02'``. Never disposition by
``review_status='staged'`` alone (~7018 rows); reconstruct flags only = **2993**.
Use :data:`STAGED_DISPOSITION_SQL` and :func:`verify_reconstruct_staged_disposition_filter`
before mass writes. ``CANDIDATE_SQL`` below is the T13 *candidate* query (pre-flag
population), not the staged-disposition query.

See cortex:notes/system/threads/1172-reconstruct-full-population-dispatch.md
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from transport_utils import DEFAULT_CORTEX_URL, make_sync_client
from universal_logging import get_logger

logger = get_logger(__name__)

MARKER = "reconstruct-2026-06-02"
REVIEW_NOTES = (
    "provenance gap: confirmed lacks locatable source; reconstruct 2026-06-02"
)

# Mass disposition on reconstruct flags (post-T13 FLAG pass) — marker required.
STAGED_DISPOSITION_SQL = """
SELECT id, entity_id, claim, reviewer, review_status
FROM assertions
WHERE superseded_by IS NULL
  AND review_status = 'staged'
  AND reviewer = ?
"""
EXPECTED_RECONSTRUCT_STAGED_COUNT = 2993
# All active staged without reviewer filter (includes other arcs) — wrong mass target.
STAGED_ONLY_WRONG_COUNT_HINT = 7018
FILES_ROOT = Path(
    __import__("os").environ.get("CORTEX_FILES_ROOT", "/mnt/torus/mcp-data/files")
).expanduser()
WORKSPACES_ROOT = Path(
    __import__("os").environ.get("WORKSPACES_ROOT", "/mnt/torus/projects")
).expanduser()

CANDIDATE_SQL = """
SELECT id, entity_id, claim, evidence, evidence_uris, chunk_id, derivation_type,
       confidence
FROM assertions
WHERE superseded_by IS NULL
  AND confidence = 'confirmed'
  AND (
    derivation_type = 'inference'
    OR evidence_uris IS NULL
    OR evidence_uris = ''
    OR evidence_uris = '[]'
  )
  AND (seeded_by IS NULL OR seeded_by != ?)
  AND (reviewer IS NULL OR reviewer != ? OR review_status != 'staged')
"""

_PATH_RE = re.compile(
    r"(?:"
    r"(?:legal|notes|dropbox|evidence)/[\w./ -]+\.(?:pdf|eml|txt|png|jpg|md)"
    r"|files://[^\s,;]+"
    r"|workspaces://[^\s,;]+"
    r"|cortex://[^\s,;]+"
    r"|https?://[^\s,;]+"
    r")",
    re.IGNORECASE,
)


@dataclass
class Candidate:
    id: int
    entity_id: str
    claim: str
    evidence: str
    evidence_uris: list[str]
    chunk_id: str | None
    derivation_type: str
    confidence: str


@dataclass
class Outcome:
    assertion_id: int
    entity_id: str
    action: str  # attach | flag | skip
    detail: str
    resolved_uri: str | None = None
    near_miss: str | None = None


def assert_disposition_dry_run_count(
    n: int,
    *,
    expected: int = EXPECTED_RECONSTRUCT_STAGED_COUNT,
) -> None:
    """Abort when a dry-run row count indicates the wrong disposition filter."""
    if abs(n - STAGED_ONLY_WRONG_COUNT_HINT) <= 100:
        print(
            f"WRONG FILTER: dry-run count={n} ~ staged-only (~{STAGED_ONLY_WRONG_COUNT_HINT}); "
            f"must include reviewer={MARKER!r} (expected {expected})",
            file=sys.stderr,
        )
        raise SystemExit(2)
    if n != expected:
        print(
            f"reconstruct staged flag count {n} != expected {expected}",
            file=sys.stderr,
        )
        raise SystemExit(2)


def verify_reconstruct_staged_disposition_filter(
    conn: sqlite3.Connection,
    *,
    expected: int = EXPECTED_RECONSTRUCT_STAGED_COUNT,
) -> int:
    """Preflight count before batch PATCH/supersede on reconstruct staged flags."""
    count = int(
        conn.execute(
            "SELECT COUNT(*) FROM assertions WHERE superseded_by IS NULL "
            "AND review_status = 'staged' AND reviewer = ?",
            (MARKER,),
        ).fetchone()[0]
    )
    assert_disposition_dry_run_count(count, expected=expected)
    return count


def _parse_uris(raw: Any) -> list[str]:
    if raw is None or raw == "" or raw == "[]":
        return []
    if isinstance(raw, list):
        return [str(u) for u in raw]
    try:
        parsed = json.loads(raw)
        return [str(u) for u in parsed] if isinstance(parsed, list) else [str(parsed)]
    except json.JSONDecodeError:
        return [str(raw)]


def _normalize_uri_typos(uri: str) -> str:
    if uri.startswith("cortex:") and not uri.startswith("cortex://"):
        return "cortex://" + uri[len("cortex:") :].lstrip("/")
    return uri


def _path_exists_for_uri(uri: str) -> tuple[bool, str | None]:
    """Return (exists, canonical_uri_for_attach)."""
    uri = _normalize_uri_typos(uri)
    if uri.startswith(("agent-bus:", "email-bridge:")):
        return False, None
    if uri.startswith("http://") or uri.startswith("https://"):
        return True, uri
    if uri.startswith("cortex://"):
        rest = uri[len("cortex://") :]
        notes_path = FILES_ROOT / rest
        if notes_path.is_file():
            return True, f"files://{notes_path}"
        try:
            from cortex_store.rag_resolver import normalize_evidence_uri

            p = normalize_evidence_uri(uri)
            return Path(p).is_file(), uri
        except Exception:
            return False, None
    if uri.startswith("workspaces://"):
        rest = uri[len("workspaces://") :]
        p = WORKSPACES_ROOT / rest
        return p.is_file(), uri if p.is_file() else None
    if uri.startswith("files://"):
        p = Path(uri[len("files://") :])
        if not p.is_absolute():
            p = FILES_ROOT / p
        exists = p.is_file()
        return exists, uri if exists else None
    # Plain relative path under FILES_ROOT (legal/evidence/…)
    p = FILES_ROOT / uri
    if p.is_file():
        if "://" in uri:
            return True, uri
        return True, uri
    return False, None


def _candidates_from_evidence_text(evidence: str) -> list[str]:
    return list(dict.fromkeys(_PATH_RE.findall(evidence or "")))


def locate_source(row: Candidate) -> tuple[str | None, str | None]:
    """Return (resolved_uri, near_miss_reason)."""
    tried: list[str] = []
    for uri in row.evidence_uris + _candidates_from_evidence_text(row.evidence):
        tried.append(uri)
        ok, canon = _path_exists_for_uri(uri)
        if ok and canon:
            return canon, None
    if row.chunk_id and row.evidence_uris:
        try:
            from cortex_store.rag_resolver import resolve_assertion_chunk

            resolve_assertion_chunk(row.id)
            return row.evidence_uris[0], None
        except Exception as exc:
            return None, f"chunk_id present but RAG resolve failed: {exc}"
    if tried:
        return None, f"uris not on disk: {tried[:3]}"
    return None, "no uris or paths extracted"


def load_candidates(
    db_path: Path, entity_ids: list[str] | None, limit: int | None
) -> list[Candidate]:
    sql = CANDIDATE_SQL
    params: list[Any] = [MARKER, MARKER]
    if entity_ids:
        placeholders = ",".join("?" * len(entity_ids))
        sql += f" AND entity_id IN ({placeholders})"
        params.extend(entity_ids)
    sql += " ORDER BY entity_id, id"
    if limit:
        sql += f" LIMIT {int(limit)}"
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(sql, params).fetchall()
    conn.close()
    out: list[Candidate] = []
    for r in rows:
        out.append(
            Candidate(
                id=int(r["id"]),
                entity_id=str(r["entity_id"]),
                claim=str(r["claim"]),
                evidence=str(r["evidence"] or ""),
                evidence_uris=_parse_uris(r["evidence_uris"]),
                chunk_id=r["chunk_id"],
                derivation_type=str(r["derivation_type"] or ""),
                confidence=str(r["confidence"]),
            )
        )
    return out


def attach(client: Any, row: Candidate, uri: str, session_id: str, agent: str) -> int:
    uris = row.evidence_uris if row.evidence_uris else [uri]
    if uri not in uris:
        uris = [uri] + [u for u in uris if u != uri]
    body = {
        "old_assertion_id": row.id,
        "entity_id": row.entity_id,
        "claim": row.claim,
        "confidence": row.confidence,
        "evidence": row.evidence,
        "evidence_uris": uris,
        "session_id": session_id,
        "agent": agent,
        "seeded_by": MARKER,
        "acknowledge_audit_gaps": ["inference_confirmed"],
    }
    r = client.post("/assertions/supersede", json=body)
    r.raise_for_status()
    return int(r.json()["new"]["id"])


def flag(client: Any, assertion_id: int) -> None:
    body = {
        "review_status": "staged",
        "reviewer": MARKER,
        "review_notes": REVIEW_NOTES,
    }
    r = client.patch(f"/assertions/{assertion_id}", json=body)
    r.raise_for_status()


def run_pass(
    *,
    db_path: Path,
    entity_ids: list[str] | None,
    limit: int | None,
    live: bool,
    session_id: str,
    agent: str,
) -> dict[str, Any]:
    candidates = load_candidates(db_path, entity_ids, limit)
    outcomes: list[Outcome] = []
    counts = {"attach": 0, "flag": 0, "skip": 0, "near_miss": 0}

    with make_sync_client(DEFAULT_CORTEX_URL, timeout=60.0) as client:
        for row in candidates:
            uri, near = locate_source(row)
            if uri:
                if not live:
                    outcomes.append(
                        Outcome(row.id, row.entity_id, "attach", "dry-run", uri, near)
                    )
                    counts["attach"] += 1
                    continue
                try:
                    new_id = attach(client, row, uri, session_id, agent)
                    outcomes.append(
                        Outcome(
                            row.id,
                            row.entity_id,
                            "attach",
                            f"superseded → {new_id}",
                            uri,
                            near,
                        )
                    )
                    counts["attach"] += 1
                except Exception as exc:
                    outcomes.append(
                        Outcome(
                            row.id,
                            row.entity_id,
                            "flag",
                            f"attach failed: {exc}",
                            None,
                            near or str(exc),
                        )
                    )
                    flag(client, row.id)
                    counts["flag"] += 1
                    if near:
                        counts["near_miss"] += 1
            else:
                if live:
                    flag(client, row.id)
                outcomes.append(
                    Outcome(
                        row.id, row.entity_id, "flag", "no locatable source", None, near
                    )
                )
                counts["flag"] += 1
                if near:
                    counts["near_miss"] += 1

    by_entity: dict[str, dict[str, int]] = {}
    for o in outcomes:
        bucket = by_entity.setdefault(o.entity_id, {"attach": 0, "flag": 0})
        bucket[o.action] = bucket.get(o.action, 0) + 1

    return {
        "total": len(candidates),
        "counts": counts,
        "by_entity": by_entity,
        "outcomes": outcomes,
        "live": live,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="T13 attach-or-flag reconstruct")
    parser.add_argument(
        "--db",
        default=str(Path.home() / ".cortex" / "cortex.db"),
        help="Path to cortex.sqlite",
    )
    parser.add_argument(
        "--legal-only",
        action="store_true",
        help="Restrict to legal slice entities (37 candidates)",
    )
    parser.add_argument("--entity-id", action="append", dest="entity_ids")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--live", action="store_true", help="Perform writes")
    parser.add_argument(
        "--verify-staged-flags",
        action="store_true",
        help=(
            "Dry-run: assert reconstruct staged flag count equals "
            f"{EXPECTED_RECONSTRUCT_STAGED_COUNT} (reviewer marker required)"
        ),
    )
    parser.add_argument("--session-id", default=f"cursor-{MARKER}")
    parser.add_argument("--agent", default="cursor")
    args = parser.parse_args()

    if args.verify_staged_flags:
        conn = sqlite3.connect(args.db)
        n = verify_reconstruct_staged_disposition_filter(conn)
        conn.close()
        print(
            json.dumps(
                {
                    "reviewer": MARKER,
                    "staged_reconstruct_flags": n,
                    "expected": EXPECTED_RECONSTRUCT_STAGED_COUNT,
                },
                indent=2,
            )
        )
        return 0

    entity_ids = args.entity_ids
    if args.legal_only:
        entity_ids = [
            "case:boe19p-flintridge-appeal-2026",
            "legal_matter:life-insurance-sale",
        ]

    result = run_pass(
        db_path=Path(args.db),
        entity_ids=entity_ids,
        limit=args.limit,
        live=args.live,
        session_id=args.session_id,
        agent=args.agent,
    )

    print(json.dumps({k: v for k, v in result.items() if k != "outcomes"}, indent=2))
    near = [o for o in result["outcomes"] if o.near_miss]
    if near:
        print("\nNear-miss / interesting (first 10):")
        for o in near[:10]:
            print(f"  {o.assertion_id} {o.entity_id}: {o.near_miss}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
