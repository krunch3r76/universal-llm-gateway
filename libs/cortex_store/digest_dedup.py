"""Digest dedup — exact hash lookup, FTS semantic candidates, and revalidation."""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3

from .claim_hash import compute_claim_hash
from .db import json_decode, query
from .digest_attach import digest_resolve_attach
from .digest_ledger import derive_valid_from_hint, map_p_class_to_derivation_confidence
from .polarity import STOP_WORDS

_SEARCH_HIT_LIMIT = 5
_FTS_TERM_LIMIT = 12
_CANDIDATE_PER_ENTITY_LIMIT = 3
_CANDIDATE_TOTAL_LIMIT = 8
_TOKEN_RE = re.compile(r"[a-z0-9]+", re.IGNORECASE)
_ISO_DATE_PREFIX = re.compile(r"^(\d{4}-\d{2}-\d{2})")
_FTS_SELECT = (
    "SELECT a.id, a.entity_id, a.claim, a.derivation_type, a.evidence_uris, "
    "a.valid_from, a.valid_until, bm25(assertions_fts) AS rank "
    "FROM assertions_fts JOIN assertions a ON a.id = assertions_fts.assertion_id "
    "WHERE assertions_fts MATCH ? AND a.superseded_by IS NULL AND a.valid_until IS NULL"
)


def dedup_candidate_entity_ids(
    *, resolved_id: str | None, search_hits: list[str], journal_entity_id: str
) -> list[str]:
    ordered: list[str] = []
    seen: set[str] = set()
    for candidate in [resolved_id, *search_hits[:_SEARCH_HIT_LIMIT], journal_entity_id]:
        if candidate and candidate not in seen:
            seen.add(candidate)
            ordered.append(candidate)
    return ordered


def claim_hash_exists(
    conn: sqlite3.Connection, entity_id: str, claim_text: str
) -> int | None:
    rows = query(
        conn,
        "SELECT id FROM assertions "
        "WHERE entity_id = ? AND claim_hash = ? AND superseded_by IS NULL",
        (entity_id, compute_claim_hash(entity_id, claim_text)),
    )
    return int(rows[0]["id"]) if rows else None


def _normalize_date(value: object | None) -> str | None:
    if value in (None, ""):
        return None
    text = str(value).strip()
    match = _ISO_DATE_PREFIX.match(text)
    return match.group(1) if match else text


def _fts_match_query(claim_text: str) -> str | None:
    tokens: list[str] = []
    for token in _TOKEN_RE.findall(claim_text):
        lowered = token.lower()
        if lowered.isdigit() or (len(lowered) >= 2 and lowered not in STOP_WORDS):
            tokens.append(lowered)
        if len(tokens) >= _FTS_TERM_LIMIT:
            break
    return " OR ".join(f'"{token}"' for token in tokens) if tokens else None


def compute_dedup_candidate_fingerprint(
    *,
    assertion_id: int,
    entity_id: str,
    claim: str,
    derivation_type: str,
    evidence_uris: list[str],
    valid_from: str | None,
    valid_until: str | None,
) -> str:
    payload = json.dumps(
        {
            "id": assertion_id,
            "entity_id": entity_id,
            "claim": claim,
            "derivation_type": derivation_type,
            "evidence_uris": sorted(evidence_uris),
            "valid_from": _normalize_date(valid_from),
            "valid_until": _normalize_date(valid_until),
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode()).hexdigest()


def _journal_sourced(evidence_uris_raw: object, journal_uri: str) -> bool:
    uris = json_decode(evidence_uris_raw, fallback=[])
    return isinstance(uris, list) and journal_uri in uris


def _candidate_from_row(
    row: dict[str, object], *, journal_uri: str, derivation_type: str
) -> dict[str, object] | None:
    if (
        row.get("valid_until") is not None
        or row.get("derivation_type") != derivation_type
    ):
        return None
    if not _journal_sourced(row.get("evidence_uris"), journal_uri):
        return None
    evidence_uris = json_decode(row.get("evidence_uris"), fallback=[])
    if not isinstance(evidence_uris, list):
        return None
    valid_from = _normalize_date(row.get("valid_from"))
    valid_until = _normalize_date(row.get("valid_until"))
    assertion_id = int(row["id"])
    entity_id = str(row["entity_id"])
    claim = str(row["claim"])
    return {
        "id": assertion_id,
        "entity_id": entity_id,
        "claim": claim,
        "derivation_type": derivation_type,
        "evidence_uris": evidence_uris,
        "valid_from": valid_from,
        "valid_until": valid_until,
        "fingerprint": compute_dedup_candidate_fingerprint(
            assertion_id=assertion_id,
            entity_id=entity_id,
            claim=claim,
            derivation_type=derivation_type,
            evidence_uris=evidence_uris,
            valid_from=valid_from,
            valid_until=valid_until,
        ),
    }


def _fts_ranked_rows(
    conn: sqlite3.Connection, fts_query: str, *, entity_id: str | None, limit: int
) -> list[dict[str, object]]:
    sql = f"{_FTS_SELECT} "
    params: list[object] = [fts_query]
    if entity_id is not None:
        sql += " AND a.entity_id = ?"
        params.append(entity_id)
    sql += " ORDER BY rank LIMIT ?"
    params.append(limit)
    try:
        return query(conn, sql, tuple(params))
    except sqlite3.OperationalError:
        return []


def fetch_semantic_dedup_candidates(
    conn: sqlite3.Connection,
    *,
    claim_text: str,
    candidate_entity_ids: list[str],
    journal_uri: str,
    expected_derivation_type: str,
) -> list[dict[str, object]]:
    fts_query = _fts_match_query(claim_text)
    if not fts_query:
        return []
    seen: set[int] = set()
    out: list[dict[str, object]] = []

    def _add_rows(rows: list[dict[str, object]], *, require_source: bool) -> None:
        for row in rows:
            if require_source and not _journal_sourced(
                row.get("evidence_uris"), journal_uri
            ):
                continue
            candidate = _candidate_from_row(
                row, journal_uri=journal_uri, derivation_type=expected_derivation_type
            )
            if candidate is None:
                continue
            cid = int(candidate["id"])
            if cid not in seen:
                seen.add(cid)
                out.append(candidate)

    for entity_id in candidate_entity_ids:
        _add_rows(
            _fts_ranked_rows(
                conn, fts_query, entity_id=entity_id, limit=_CANDIDATE_PER_ENTITY_LIMIT
            ),
            require_source=False,
        )
        if len(out) >= _CANDIDATE_TOTAL_LIMIT:
            return out
    exclude = set(candidate_entity_ids)
    for row in _fts_ranked_rows(
        conn, fts_query, entity_id=None, limit=max(_CANDIDATE_TOTAL_LIMIT * 4, 20)
    ):
        if str(row["entity_id"]) not in exclude:
            _add_rows([row], require_source=True)
        if len(out) >= _CANDIDATE_TOTAL_LIMIT:
            break
    return out


def enrich_claim_batch_dedup_candidates(
    conn: sqlite3.Connection,
    claim_batch: dict[str, object],
    *,
    journal_uri: str,
    journal_entity_id: str,
) -> dict[str, object]:
    claims_in = claim_batch.get("claims")
    if not isinstance(claims_in, list):
        return claim_batch
    enriched: list[dict[str, object]] = []
    for claim in claims_in:
        if not isinstance(claim, dict):
            enriched.append({"claim": claim, "dedup_candidates": []})
            continue
        item = dict(claim)
        if item.get("canonicality") == "prose":
            item["dedup_candidates"] = []
        else:
            resolved_id, search_hits = digest_resolve_attach(
                conn,
                str(attach_hint) if (attach_hint := item.get("attach_hint")) else None,
            )
            try:
                derivation_type, _ = map_p_class_to_derivation_confidence(
                    str(item["p_class"])
                )
            except (KeyError, ValueError):
                item["dedup_candidates"] = []
            else:
                item["dedup_candidates"] = fetch_semantic_dedup_candidates(
                    conn,
                    claim_text=str(item.get("claim", "")),
                    candidate_entity_ids=dedup_candidate_entity_ids(
                        resolved_id=resolved_id,
                        search_hits=search_hits,
                        journal_entity_id=journal_entity_id,
                    ),
                    journal_uri=journal_uri,
                    expected_derivation_type=derivation_type,
                )
        enriched.append(item)
    out = dict(claim_batch)
    out["claims"] = enriched
    return out


def revalidate_semantic_selection(
    conn: sqlite3.Connection,
    *,
    assertion_id: int,
    expected_fingerprint: str,
    journal_uri: str,
    expected_derivation_type: str,
    claim_valid_from: str | None,
    target_entity_id: str | None,
    target_is_resolved: bool,
) -> bool:
    rows = query(
        conn,
        "SELECT id, entity_id, claim, derivation_type, evidence_uris, "
        "valid_from, valid_until, superseded_by FROM assertions WHERE id = ?",
        (assertion_id,),
    )
    if not rows or rows[0].get("superseded_by") is not None:
        return False
    candidate = _candidate_from_row(
        rows[0],
        journal_uri=journal_uri,
        derivation_type=expected_derivation_type,
    )
    if candidate is None or candidate["fingerprint"] != expected_fingerprint:
        return False
    cand_vf = candidate.get("valid_from")
    claim_vf = _normalize_date(claim_valid_from)
    if cand_vf and claim_vf and cand_vf != claim_vf:
        return False
    return not target_is_resolved or candidate["entity_id"] == target_entity_id


def resolve_staging_dedup_skip(
    conn: sqlite3.Connection,
    *,
    claim: dict[str, object],
    resolved_id: str | None,
    source_uri: str,
    entity_id: str,
) -> int | None:
    if resolved_id:
        existing = claim_hash_exists(conn, resolved_id, str(claim["claim"]))
        if existing is not None:
            return existing
        target_is_resolved, target_entity_id = True, resolved_id
    else:
        target_is_resolved, target_entity_id = False, entity_id
    duplicate_id = claim.get("duplicate_of")
    fingerprint = claim.get("dedup_candidate_fingerprint")
    if (
        claim.get("verify_verdict") != "pass"
        or not isinstance(duplicate_id, int)
        or not isinstance(fingerprint, str)
        or not fingerprint
    ):
        return None
    try:
        derivation_type, _ = map_p_class_to_derivation_confidence(str(claim["p_class"]))
    except (KeyError, ValueError):
        return None
    if not revalidate_semantic_selection(
        conn,
        assertion_id=duplicate_id,
        expected_fingerprint=fingerprint,
        journal_uri=source_uri,
        expected_derivation_type=derivation_type,
        claim_valid_from=derive_valid_from_hint(claim),
        target_entity_id=target_entity_id,
        target_is_resolved=target_is_resolved,
    ):
        return None
    return duplicate_id
