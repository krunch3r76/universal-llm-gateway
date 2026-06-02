"""Option (b) — persist host-derived ``assertions.credibility`` for external http(s) sources.

Materializes ``credibility_for_keys`` (§3c) into the column when NULL so shadow
derivation and audits see explicit Ψ bands. Unlisted http(s) hosts stay NULL
(unrated at compute time). Idempotent; never overwrites a non-NULL stored value.

Operator binding: assertion 12013 (a)+(b); assertion 12016 option (b) pending →
this backfill. Does NOT retarget ``substantiation_sync`` or flip entity status.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from urllib.parse import urlparse

from universal_logging import get_logger

from .confidence_policy import (
    credibility_for_keys,
    host_credibility,
    is_eligible_review_status,
    normalized_source_key,
)
from .db import json_decode, table_exists

logger = get_logger("cortex-api.assertion_credibility_backfill")


@dataclass
class CredibilityBackfillCounts:
    """Per-run counts for external-source credibility backfill."""

    assertions_updated: int = 0
    assertions_skipped: int = 0
    by_band: dict[str, int] = field(default_factory=dict)
    by_host: dict[str, int] = field(default_factory=dict)
    distinct_http_hosts_seen: int = 0
    unlisted_http_host_refs: int = 0


def assertions_have_credibility_column(conn: sqlite3.Connection) -> bool:
    if not table_exists(conn, "assertions"):
        return False
    cols = {r[1] for r in conn.execute("PRAGMA table_info(assertions)").fetchall()}
    return "credibility" in cols


def external_http_source_keys(evidence_uris_raw: str | None) -> tuple[str, ...]:
    """Normalized host keys from genuine http(s) citation URIs only."""
    uris = json_decode(evidence_uris_raw, fallback=[]) or []
    if not isinstance(uris, list):
        return ()
    keys: set[str] = set()
    for uri in uris:
        raw = str(uri).strip()
        if not raw:
            continue
        parsed = urlparse(raw)
        if parsed.scheme in ("http", "https") and parsed.netloc:
            keys.add(normalized_source_key(raw))
    return tuple(sorted(keys))


def planned_credibility_band(
    *,
    credibility: str | None,
    evidence_uris: str | None,
    review_status: str | None,
    superseded_by: str | None = None,
) -> str | None:
    """Band to persist, or ``None`` when this row should not be updated."""
    if superseded_by is not None:
        return None
    if not is_eligible_review_status(review_status):
        return None
    if credibility is not None:
        return None
    keys = external_http_source_keys(evidence_uris)
    if not keys:
        return None
    return credibility_for_keys(keys)


def _listed_hosts_for_keys(keys: tuple[str, ...]) -> list[str]:
    return [
        k
        for k in keys
        if host_credibility(k) is not None or k.endswith(".gov") or k == "gov"
    ]


def run_external_credibility_backfill(
    conn: sqlite3.Connection,
    *,
    dry_run: bool = True,
) -> CredibilityBackfillCounts:
    """Backfill NULL credibility from http(s) host policy; skip unlisted hosts."""
    counts = CredibilityBackfillCounts()
    if not assertions_have_credibility_column(conn):
        logger.warning("assertions.credibility column absent — skipping backfill")
        return counts

    rows = conn.execute(
        "SELECT id, credibility, evidence_uris, review_status, superseded_by "
        "FROM assertions WHERE superseded_by IS NULL"
    ).fetchall()

    http_hosts_seen: set[str] = set()

    for row in rows:
        keys = external_http_source_keys(row["evidence_uris"])
        for k in keys:
            http_hosts_seen.add(k)

        band = planned_credibility_band(
            credibility=row["credibility"],
            evidence_uris=row["evidence_uris"],
            review_status=row["review_status"],
            superseded_by=row["superseded_by"],
        )
        if band is None:
            if keys and credibility_for_keys(keys) is None:
                counts.unlisted_http_host_refs += len(keys)
            continue

        counts.assertions_updated += 1
        counts.by_band[band] = counts.by_band.get(band, 0) + 1
        for host in _listed_hosts_for_keys(keys):
            counts.by_host[host] = counts.by_host.get(host, 0) + 1

        if dry_run:
            logger.info(
                "dry-run credibility backfill id=%s band=%s hosts=%s",
                row["id"],
                band,
                keys,
            )
            continue

        conn.execute(
            "UPDATE assertions SET credibility = ? WHERE id = ? AND credibility IS NULL",
            (band, row["id"]),
        )

    counts.distinct_http_hosts_seen = len(http_hosts_seen)

    if not dry_run and counts.assertions_updated:
        conn.commit()
        logger.info(
            "External credibility backfill committed: updated=%d bands=%s",
            counts.assertions_updated,
            counts.by_band,
        )
    return counts


__all__ = [
    "CredibilityBackfillCounts",
    "assertions_have_credibility_column",
    "external_http_source_keys",
    "planned_credibility_band",
    "run_external_credibility_backfill",
]
